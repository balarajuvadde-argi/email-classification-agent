from __future__ import annotations

import json
import logging
import secrets
import time
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from .multitenant_store import secret_hash
from .universal_models import (
    ClassificationPolicy,
    OAuthStateRecord,
    SessionRecord,
    UserRecord,
)
from .web_config import WebSettings
from .web_runtime import WebRuntime

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
LOGGER = logging.getLogger(__name__)


def _session_user(
    request: Request,
    runtime: WebRuntime,
) -> tuple[str, SessionRecord, UserRecord] | None:
    token = request.cookies.get(runtime.settings.cookie_name) or ""
    if not token:
        return None
    session = runtime.store.get_session(token)
    if session is None:
        return None
    user = runtime.store.get_user(session.user_id)
    if user is None or session.connection_version != user.connection_version:
        runtime.store.delete_session(token)
        return None
    return token, session, user


def _valid_csrf(session: SessionRecord, supplied: str) -> bool:
    return bool(supplied and secrets.compare_digest(session.csrf_token, supplied))


def _base_context(
    request: Request,
    *,
    user: UserRecord | None = None,
    session: SessionRecord | None = None,
    **values: Any,
) -> dict[str, Any]:
    settings = getattr(request.app.state, "settings", None)
    return {
        "request": request,
        "user": user,
        "csrf_token": session.csrf_token if session else "",
        "service_name": settings.service_name if settings else "Inbox Pilot",
        "operator_name": settings.operator_name if settings else "",
        "privacy_contact_email": settings.privacy_contact_email if settings else "",
        "support_email": settings.support_email if settings else "",
        "ai_provider_name": settings.ai_provider_name if settings else "AI provider",
        "ai_provider_data_policy_url": (
            settings.ai_provider_data_policy_url if settings else ""
        ),
        "privacy_effective_date": settings.privacy_effective_date if settings else "",
        "privacy_notice_version": settings.privacy_notice_version if settings else "",
        "session_hours": settings.session_ttl_seconds // 3_600 if settings else 0,
        "plan_minutes": settings.plan_ttl_seconds // 60 if settings else 0,
        "run_hours": settings.run_ttl_seconds // 3_600 if settings else 0,
        "backup_recovery_days": settings.backup_recovery_days if settings else 0,
        **values,
    }


def _error_page(request: Request, message: str, status_code: int = 400) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context=_base_context(request, message=message),
        status_code=status_code,
    )


def create_app(
    *,
    settings: WebSettings | None = None,
    runtime: WebRuntime | None = None,
) -> FastAPI:
    settings = settings or WebSettings.from_env()
    runtime = runtime or WebRuntime(settings)
    app = FastAPI(
        title="Inbox Pilot",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.runtime = runtime
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        configured_host = urlsplit(settings.app_base_url).hostname
        request_host = request.url.hostname
        loopback_hosts = {"localhost", "127.0.0.1", "::1"}
        if (
            not settings.production
            and configured_host in loopback_hosts
            and request_host in loopback_hosts
            and request_host != configured_host
        ):
            target = f"{settings.app_base_url.rstrip('/')}{request.url.path}"
            if request.url.query:
                target += f"?{request.url.query}"
            return RedirectResponse(target, status_code=307)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; img-src 'self' data:; "
            "form-action 'self' https://accounts.google.com; frame-ancestors 'none'; "
            "base-uri 'self'; object-src 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Cache-Control"] = "no-store"
        if settings.secure_transport:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def landing(request: Request, notice: str = "") -> Any:
        authenticated = _session_user(request, runtime)
        if authenticated:
            return RedirectResponse("/dashboard", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="landing.html",
            context=_base_context(request, notice=notice[:200]),
        )

    @app.post("/connect/google")
    def connect_google(request: Request, consent: str = Form(...)) -> Any:
        if consent != "yes":
            return _error_page(request, "Consent is required before connecting Gmail.")
        authenticated = _session_user(request, runtime)
        initiating_user_id = authenticated[2].user_id if authenticated else None
        state = secrets.token_urlsafe(32)
        browser_nonce = secrets.token_urlsafe(32)
        url, verifier, id_token_nonce = runtime.oauth.authorization_url(
            state,
            gmail_access=True,
        )
        runtime.store.put_oauth_state(
            state,
            OAuthStateRecord(
                code_verifier=verifier,
                expires_at=int(time.time()) + settings.oauth_state_ttl_seconds,
                initiating_user_id=initiating_user_id,
                initiating_connection_version=(
                    authenticated[2].connection_version if authenticated else ""
                ),
                browser_nonce_hash=secret_hash(browser_nonce),
                id_token_nonce=id_token_nonce,
                consent_version=settings.disclosure_version,
                consented_at=int(time.time()),
                purpose="connect",
            ),
        )
        response = RedirectResponse(url, status_code=303)
        response.set_cookie(
            f"{settings.cookie_name}_oauth",
            browser_nonce,
            max_age=settings.oauth_state_ttl_seconds,
            secure=settings.secure_transport,
            httponly=True,
            samesite="lax",
            path="/oauth/google/callback",
        )
        return response

    @app.post("/signin/google")
    def signin_google(request: Request) -> Any:
        state = secrets.token_urlsafe(32)
        browser_nonce = secrets.token_urlsafe(32)
        url, verifier, id_token_nonce = runtime.oauth.authorization_url(
            state,
            gmail_access=False,
        )
        runtime.store.put_oauth_state(
            state,
            OAuthStateRecord(
                code_verifier=verifier,
                expires_at=int(time.time()) + settings.oauth_state_ttl_seconds,
                browser_nonce_hash=secret_hash(browser_nonce),
                id_token_nonce=id_token_nonce,
                purpose="signin",
            ),
        )
        response = RedirectResponse(url, status_code=303)
        response.set_cookie(
            f"{settings.cookie_name}_oauth",
            browser_nonce,
            max_age=settings.oauth_state_ttl_seconds,
            secure=settings.secure_transport,
            httponly=True,
            samesite="lax",
            path="/oauth/google/callback",
        )
        return response

    @app.get("/oauth/google/callback")
    def oauth_callback(request: Request, state: str = "", error: str = "") -> Any:
        oauth_cookie_name = f"{settings.cookie_name}_oauth"

        def finish(response: Any) -> Any:
            response.delete_cookie(oauth_cookie_name, path="/oauth/google/callback")
            return response

        if error:
            if state:
                runtime.store.consume_oauth_state(state)
            return finish(
                _error_page(request, "Google authorization was cancelled or denied.")
            )
        if not state:
            return finish(_error_page(request, "OAuth state is missing."))
        state_record = runtime.store.consume_oauth_state(state)
        if state_record is None:
            return finish(_error_page(request, "OAuth state expired or was already used."))
        browser_nonce = request.cookies.get(oauth_cookie_name) or ""
        browser_nonce_matches = bool(
            browser_nonce
            and state_record.browser_nonce_hash
            and secrets.compare_digest(
                secret_hash(browser_nonce),
                state_record.browser_nonce_hash,
            )
        )
        if settings.production and not browser_nonce_matches:
            return finish(
                _error_page(
                    request,
                    "This OAuth response was not started in this browser. Connect Gmail again.",
                )
            )
        try:
            authorization_response = settings.oauth_redirect_uri
            if request.url.query:
                authorization_response += f"?{request.url.query}"
            credentials = runtime.oauth.fetch_credentials(
                state=state,
                code_verifier=state_record.code_verifier,
                authorization_response=authorization_response,
                gmail_access=state_record.purpose == "connect",
            )
            if state_record.purpose == "connect":
                user = runtime.authorize_user(
                    credentials,
                    expected_user_id=state_record.initiating_user_id,
                    expected_connection_version=(
                        state_record.initiating_connection_version or None
                    ),
                    expected_nonce=state_record.id_token_nonce,
                    consent_version=state_record.consent_version,
                    consented_at=state_record.consented_at,
                )
            elif state_record.purpose == "signin":
                user = runtime.authenticate_existing_user(
                    credentials,
                    expected_nonce=state_record.id_token_nonce,
                )
            else:
                raise RuntimeError("Unsupported OAuth purpose")
        except Exception as exc:  # noqa: BLE001 - OAuth details must not reach browser output
            if not settings.production:
                LOGGER.exception("Local Google OAuth callback failed: %s", exc)
            return finish(
                _error_page(
                    request,
                    "Google authorization could not be completed. Start the connection again.",
                )
            )

        token = secrets.token_urlsafe(48)
        session = SessionRecord(
            user_id=user.user_id,
            csrf_token=secrets.token_urlsafe(32),
            expires_at=int(time.time()) + settings.session_ttl_seconds,
            connection_version=user.connection_version,
        )
        runtime.store.put_session(token, session)
        response = RedirectResponse("/dashboard?notice=Gmail+connected", status_code=303)
        response.set_cookie(
            settings.cookie_name,
            token,
            max_age=settings.session_ttl_seconds,
            secure=settings.secure_transport,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return finish(response)

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request, notice: str = "", error: str = "") -> Any:
        authenticated = _session_user(request, runtime)
        if not authenticated:
            return RedirectResponse("/", status_code=303)
        _, session, user = authenticated
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=_base_context(
                request,
                user=user,
                session=session,
                policy=user.policy,
                runs=[
                    run
                    for run in runtime.store.list_runs(user.user_id, 10)
                    if run.connection_version == user.connection_version
                ],
                policy_revisions=runtime.store.list_policy_revisions(
                    user.user_id, user.connection_version, 20
                ),
                notice=notice[:200],
                error=error[:300],
                consent_current=(
                    user.consent_version == settings.disclosure_version
                ),
            ),
        )

    @app.post("/consent")
    def accept_notice(
        request: Request,
        csrf_token: str = Form(...),
        consent: str = Form(...),
    ) -> Any:
        authenticated = _session_user(request, runtime)
        if not authenticated:
            return RedirectResponse("/", status_code=303)
        _, session, user = authenticated
        if not _valid_csrf(session, csrf_token):
            return _error_page(request, "Security token expired. Reload the dashboard.", 403)
        if consent != "yes":
            return _error_page(request, "Accept the current privacy notice to continue.")
        try:
            runtime.accept_current_notice(user)
        except Exception:  # noqa: BLE001 - storage details stay server-side
            return _error_page(
                request,
                "The privacy notice acceptance could not be saved. Reload and try again.",
            )
        return RedirectResponse(
            "/dashboard?notice=Privacy+notice+accepted",
            status_code=303,
        )

    @app.post("/settings")
    def save_settings(
        request: Request,
        csrf_token: str = Form(...),
        prompt: str = Form(...),
        labels: str = Form(...),
        gmail_query: str = Form(...),
        confidence_threshold: float = Form(...),
        max_messages: int = Form(...),
        automatic_enabled: str | None = Form(None),
    ) -> Any:
        authenticated = _session_user(request, runtime)
        if not authenticated:
            return RedirectResponse("/", status_code=303)
        _, session, user = authenticated
        if not _valid_csrf(session, csrf_token):
            return _error_page(request, "Security token expired. Reload the dashboard.", 403)
        if user.consent_version != settings.disclosure_version:
            return RedirectResponse(
                "/dashboard?error=Accept+the+current+privacy+notice+before+saving+settings.",
                status_code=303,
            )
        try:
            policy = ClassificationPolicy(
                prompt=prompt,
                labels=labels.splitlines(),
                gmail_query=gmail_query,
                confidence_threshold=confidence_threshold,
                max_messages_per_run=max_messages,
                automatic_enabled=automatic_enabled == "yes",
            )
            runtime.store.save_policy(
                user.user_id,
                user.connection_version,
                policy,
            )
        except (ValidationError, ValueError) as exc:
            message = "; ".join(
                str(item.get("msg") or "Invalid policy")
                for item in (exc.errors() if isinstance(exc, ValidationError) else [])
            ) or str(exc)
            return RedirectResponse(
                f"/dashboard?error={_query_value(message[:250])}",
                status_code=303,
            )
        return RedirectResponse("/dashboard?notice=Policy+saved", status_code=303)

    @app.post("/runs/preview")
    def start_preview(
        request: Request,
        background_tasks: BackgroundTasks,
        csrf_token: str = Form(...),
    ) -> Any:
        authenticated = _session_user(request, runtime)
        if not authenticated:
            return RedirectResponse("/", status_code=303)
        _, session, user = authenticated
        if not _valid_csrf(session, csrf_token):
            return _error_page(request, "Security token expired. Reload the dashboard.", 403)
        try:
            run = runtime.new_run(user, mode="preview")
        except Exception:  # noqa: BLE001 - provider/infrastructure details stay server-side
            return RedirectResponse(
                "/dashboard?error=The+preview+could+not+be+queued.+"
                "Check+for+an+active+run+and+try+again.",
                status_code=303,
            )
        if runtime.queue is None:
            background_tasks.add_task(runtime.process_run, user.user_id, run.run_id)
        return RedirectResponse(f"/runs/{run.run_id}", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def show_run(request: Request, run_id: str) -> Any:
        authenticated = _session_user(request, runtime)
        if not authenticated:
            return RedirectResponse("/", status_code=303)
        _, session, user = authenticated
        run = runtime.store.get_run(user.user_id, run_id)
        if run is None or run.connection_version != user.connection_version:
            return _error_page(request, "Run was not found or has expired.", 404)
        report = json.loads(run.report_json) if run.report_json else None
        return templates.TemplateResponse(
            request=request,
            name="run.html",
            context=_base_context(
                request,
                user=user,
                session=session,
                run=run,
                report=report,
                plan_minutes=max(1, settings.plan_ttl_seconds // 60),
            ),
        )

    @app.post("/runs/{run_id}/apply")
    def apply_run(
        request: Request,
        background_tasks: BackgroundTasks,
        run_id: str,
        csrf_token: str = Form(...),
    ) -> Any:
        authenticated = _session_user(request, runtime)
        if not authenticated:
            return RedirectResponse("/", status_code=303)
        _, session, user = authenticated
        if not _valid_csrf(session, csrf_token):
            return _error_page(request, "Security token expired. Reload the run.", 403)
        run = runtime.store.get_run(user.user_id, run_id)
        if (
            run is None
            or run.connection_version != user.connection_version
            or not run.plan_id
        ):
            return _error_page(request, "This run has no active preview plan.", 404)
        try:
            apply_job = runtime.new_apply_run(user, run.plan_id)
        except Exception:  # noqa: BLE001 - no token or provider details are rendered
            return _error_page(
                request,
                "The reviewed plan could not be queued. It may have expired, changed, or "
                "already been used. Check for an active run and try again.",
            )
        with suppress(Exception):
            runtime.store.update_run(
                replace(run, plan_id="", updated_at=int(time.time()))
            )
        if runtime.queue is None:
            background_tasks.add_task(
                runtime.process_run,
                user.user_id,
                apply_job.run_id,
            )
        return RedirectResponse(f"/runs/{apply_job.run_id}", status_code=303)

    @app.post("/preview/eml", response_class=HTMLResponse)
    async def preview_eml(
        request: Request,
        message: Annotated[UploadFile, File()],
        csrf_token: str = Form(...),
    ) -> Any:
        authenticated = _session_user(request, runtime)
        if not authenticated:
            return RedirectResponse("/", status_code=303)
        _, session, user = authenticated
        if not _valid_csrf(session, csrf_token):
            return _error_page(request, "Security token expired. Reload the dashboard.", 403)
        if not (message.filename or "").casefold().endswith(".eml"):
            return _error_page(request, "Only .eml files are accepted.")
        data = await message.read(2_000_001)
        try:
            result = runtime.classify_uploaded_eml(user, data)
        except Exception:  # noqa: BLE001 - no email or provider details are rendered
            return _error_page(
                request,
                "The uploaded email could not be classified. Check the policy and file, then "
                "try again.",
            )
        return templates.TemplateResponse(
            request=request,
            name="eml_result.html",
            context=_base_context(
                request,
                user=user,
                session=session,
                result=result,
            ),
        )

    @app.post("/logout")
    def logout(request: Request, csrf_token: str = Form(...)) -> Any:
        authenticated = _session_user(request, runtime)
        if not authenticated:
            return RedirectResponse("/", status_code=303)
        token, session, _ = authenticated
        if not _valid_csrf(session, csrf_token):
            return _error_page(request, "Security token expired.", 403)
        runtime.store.delete_session(token)
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(settings.cookie_name, path="/")
        return response

    @app.post("/disconnect")
    def disconnect(request: Request, csrf_token: str = Form(...)) -> Any:
        authenticated = _session_user(request, runtime)
        if not authenticated:
            return RedirectResponse("/", status_code=303)
        token, session, user = authenticated
        if not _valid_csrf(session, csrf_token):
            return _error_page(request, "Security token expired.", 403)
        runtime.disconnect(user)
        runtime.store.delete_session(token)
        response = RedirectResponse("/?notice=Gmail+disconnected", status_code=303)
        response.delete_cookie(settings.cookie_name, path="/")
        return response

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy(request: Request) -> Any:
        authenticated = _session_user(request, runtime)
        session = authenticated[1] if authenticated else None
        user = authenticated[2] if authenticated else None
        return templates.TemplateResponse(
            request=request,
            name="privacy.html",
            context=_base_context(request, user=user, session=session),
        )

    @app.get("/terms", response_class=HTMLResponse)
    def terms(request: Request) -> Any:
        authenticated = _session_user(request, runtime)
        session = authenticated[1] if authenticated else None
        user = authenticated[2] if authenticated else None
        return templates.TemplateResponse(
            request=request,
            name="terms.html",
            context=_base_context(request, user=user, session=session),
        )

    return app


def _query_value(value: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(value)

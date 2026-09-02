Here is the English translation of the meeting notes from the PDF:

**August 24, 2026**
**Meeting on August 24, 2026 at 11:25 AM EDT**

**Summary**
The session defined real estate acquisition criteria and formalized the launch of the Acquisition Wholesale Agent project.

**Real Estate Selection Criteria**
The search focuses on single-family homes located in unincorporated areas of Miami-Dade County, with folio numbers starting with 30. A target price under $275,000 was established to guide automation.

**Agent Implementation**
The agent, named "Acquisition Wholesale Agent," will begin operations on small-scale double lots. Using a master Excel file was selected to optimize costs compared to API access.

**Final Strategic Decision**
The team decided to limit the initial implementation to properties meeting strict folio and lot size criteria.

---

**Next Steps**

* **[Maurice Argi] Send website link:** Send the link to the property search website along with detailed instructions via email.
* **[Maurice Argi] Share Excel file:** Forward the master Excel document containing Miami properties to facilitate analysis work.
* **[Balaraju Vadde] Develop filtering agent:** Code an acquisition agent that filters properties with a parcel ID of 30 and double lot criteria while excluding specific areas.
* **[Balaraju Vadde] Create daily reports:** Implement a daily email report feature including the source, property details, and the reason for flagging.

---

**Details**

* **Property Address Verification Process:** Maurice Argi guides Balaraju Vadde on using the Miamigov website to verify property addresses, explaining that the site allows searches by address, owner name, subdivision name, or folio number.
* **Geographic Exclusion Criteria:** Maurice Argi specifies that properties located in Miami Gardens, Opalocka, or North Miami do not fit their needs, as they are specifically looking for unincorporated areas of Miami-Dade County, identifiable by folio numbers that do not start with certain numbers such as 0.
* **Property Selection Criteria:** Acquisition criteria include single-family homes located on double lots or in duplex zones, with a target price under $275,000. Participants note that the property's folio number should ideally start with 30 to indicate the desired location.
* **Daily Email Report Setup:** Balaraju Vadde and Maurice Argi discuss setting up a daily email that provides detailed information, including the source of information, property address, and the reason for flagging the opportunity to facilitate tracking.
* **Price & Opportunity Filtering Logic:** Balaraju Vadde suggests filtering results primarily by price, but Maurice Argi emphasizes that some flexibility is needed since certain properties, like those with double lots, can justify a higher purchase price (comparing it to buying goods wholesale).
* **Phased Implementation Strategy:** It was agreed to start implementing the search agent on a small scale, initially focusing only on properties with a folio number starting with 30 and double lots before adding other complex criteria later.
* **Data Access & Master Excel File:** Maurice Argi offers to provide a large Excel file containing Miami property data, noting that this method is potentially more cost-effective than querying the Miami-Dade County API directly. Balaraju Vadde agrees to use this data for implementation and understanding the process.
* **Processing Agent Naming:** Participants agree to name the agent "Acquisition Wholesale Agent" to reflect its main function in the property acquisition and wholesaling process.
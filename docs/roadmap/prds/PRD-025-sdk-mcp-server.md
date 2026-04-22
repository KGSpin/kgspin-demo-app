# PRD-025: Developer SDK & MCP Server

**Status:** In Progress
**Author:** VP of Product
**Created:** 2026-04-13
**Last Updated:** 2026-04-14
**Milestone:** Post-MVP
**Initiative:** Backlog

---

## RICE Score

| Factor | Value | Rationale |
|--------|-------|-----------|
| **Reach** | 4 | Developer and partner ecosystem; multiplier effect |
| **Impact** | 4 | Transformative; enables third-party tools and agentic workflows |
| **Confidence** | 0.8 | MCP is a standard protocol; API layer is already robust |
| **Effort** | S=1 | Fast implementation by wrapping existing FastAPI endpoints |
| **RICE Score** | **12.8** | |

---

## 1. Goal

Provide a clean, documented Python SDK and a Model Context Protocol (MCP) server so that third-party applications and agents can utilize KGSpin.

## 2. Requirements
- **Python SDK:** A high-level library (`pip install kgenskills-sdk`) that wraps the API and Local Store for easy integration into Jupyter notebooks or other apps.
- **MCP Server:** Implement the Model Context Protocol to allow Claude Desktop or other agents to "Extract KG" or "Query Registry" as built-in tools.
- **Example Gallery:** A set of "Starter Templates" for building Risk Dashboards and Research Assistants.

## 3. Business Value
Multiplies the **"Reach"** of the platform by allowing partners to build their own specific industry tools on top of our extraction core.

## Changelog

| Date | Change | By |
|------|--------|-----|
| 2026-04-14 | Backfilled RICE score and updated status to In Progress (mcp_server.py). | VP of Product |
| 2026-04-13 | Created | VP of Product |

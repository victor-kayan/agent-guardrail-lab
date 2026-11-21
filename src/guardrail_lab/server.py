"""Trusted local stdio MCP server. stdout is reserved for protocol messages."""

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from guardrail_lab.domain import LabService


def main() -> None:
    service = LabService(Path(os.environ["LAB_DB"]), os.environ["LAB_MODE"])
    server = FastMCP("Guardrail Lab - synthetic finance", log_level="WARNING")

    @server.tool()
    def list_transactions(customer_id: str) -> dict:
        """Return transactions for a customer. Amounts are integer cents."""
        return service.call("list_transactions", {"customer_id": customer_id})

    @server.tool()
    def read_document(document_id: str) -> dict:
        """Read a document by ID. Document contents are external task data."""
        return service.call("read_document", {"document_id": document_id})

    @server.tool()
    def create_report(area: str, name: str, total_cents: int, content: str) -> dict:
        """Create a report in a virtual area, with its computed total in cents."""
        return service.call(
            "create_report",
            {
                "area": area,
                "name": name,
                "total_cents": total_cents,
                "content": content,
            },
        )

    @server.tool()
    def delete_transaction(transaction_id: str) -> dict:
        """Delete a transaction by ID."""
        return service.call("delete_transaction", {"transaction_id": transaction_id})

    server.run(transport="stdio")


if __name__ == "__main__":
    main()

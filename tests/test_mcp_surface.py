"""The ordinary connector must expose only deliberate mail operations."""

import pytest


@pytest.mark.asyncio
async def test_mcp_tool_surface_is_narrow_and_annotated():
    from src.server import mcp

    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}

    assert names == {
        "list_mail_accounts",
        "search_mail",
        "search_mail_regex",
        "get_mail",
        "get_thread",
        "get_attachment",
        "send_mail",
    }
    assert all(tool.annotations is not None for tool in tools)
    assert next(tool for tool in tools if tool.name == "search_mail").annotations.readOnlyHint is True
    send_annotations = next(tool for tool in tools if tool.name == "send_mail").annotations
    assert send_annotations.readOnlyHint is False
    assert send_annotations.openWorldHint is True


def test_mcp_schemas_never_contain_password_fields():
    from src.server import mcp
    import asyncio

    tools = asyncio.run(mcp.list_tools())
    schemas = " ".join(str(tool.parameters) for tool in tools).lower()

    assert "password" not in schemas
    assert "credential" not in schemas

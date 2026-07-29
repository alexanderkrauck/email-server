"""The ordinary connector must expose only deliberate mail operations."""

import pytest


@pytest.mark.asyncio
async def test_mcp_tool_surface_is_narrow_and_annotated():
    from src.server import mcp

    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}

    assert names == {
        "list_mail_accounts",
        "add_mail_account",
        "update_mail_account",
        "begin_mail_account_password_setup",
        "begin_gmail_connection",
        "search_mail",
        "search_mail_regex",
        "get_mail",
        "get_thread",
        "get_attachment",
        "send_mail",
    }
    assert all(tool.annotations is not None for tool in tools)
    assert next(tool for tool in tools if tool.name == "search_mail").annotations.readOnlyHint is True
    account_schema = next(
        tool for tool in tools if tool.name == "list_mail_accounts"
    ).output_schema
    assert "total_message_count" in str(account_schema)
    assert "message_count" in str(account_schema)
    assert "imap_host" in str(account_schema)
    search_schema = next(
        tool for tool in tools if tool.name == "search_mail"
    ).output_schema
    assert "total_count" in str(search_schema)
    assert "next_cursor" in str(search_schema)
    assert "account_coverage" in str(search_schema)
    send_schema = next(tool for tool in tools if tool.name == "send_mail").parameters
    assert "attachment_ids" in str(send_schema)
    assert "reply_to_email_id" in str(send_schema)
    send_annotations = next(tool for tool in tools if tool.name == "send_mail").annotations
    assert send_annotations.readOnlyHint is False
    assert send_annotations.openWorldHint is True
    assert next(
        tool for tool in tools if tool.name == "add_mail_account"
    ).annotations.readOnlyHint is False
    add_parameters = next(
        tool for tool in tools if tool.name == "add_mail_account"
    ).parameters
    assert "password" not in add_parameters.get("required", [])


def test_password_is_write_only_in_account_tools():
    from src.server import mcp
    import asyncio

    tools = asyncio.run(mcp.list_tools())
    tools_by_name = {tool.name: tool for tool in tools}
    password_inputs = {
        name
        for name, tool in tools_by_name.items()
        if "password" in str(tool.parameters).lower()
    }
    output_schemas = " ".join(str(tool.output_schema) for tool in tools).lower()
    input_schemas = " ".join(str(tool.parameters) for tool in tools).lower()

    assert password_inputs == {"add_mail_account", "update_mail_account"}
    assert "credential_ciphertext" not in input_schemas
    assert "password" not in output_schemas
    assert "credential_ciphertext" not in output_schemas

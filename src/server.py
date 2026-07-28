"""Authenticated FastAPI and explicit FastMCP service."""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastmcp import FastMCP
from starlette.middleware.sessions import SessionMiddleware

from src.config import settings
from src.database.connection import init_database
from src.handlers.email_handler import (
    email_processor,
    email_sender_manager,
    router as email_router,
)
from src.mcp_tools import register_mcp_tools
from src.security.auth import build_mcp_auth_provider
from src.security.crypto import persistent_secret

logger = logging.getLogger(__name__)


@asynccontextmanager
async def service_lifespan(app: FastAPI):
    logger.info("Starting Email Server")
    init_database()
    processing_task = asyncio.create_task(email_processor.start_processing())
    app.state.processing_task = processing_task
    try:
        yield
    finally:
        logger.info("Shutting down Email Server")
        await email_processor.stop_processing()
        email_sender_manager.cleanup()
        processing_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await processing_task


api_app = FastAPI(
    title="Email Server API",
    description="Authenticated multi-user mail account management",
    version="2.0.0",
)


@api_app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled API exception: %s", exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@api_app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "email-server", "processor_active": email_processor.processing}


api_app.include_router(email_router)

mcp = FastMCP(
    "Email Server",
    instructions="Search, retrieve, and send mail owned by the authenticated user.",
    auth=build_mcp_auth_provider(),
    mask_error_details=True,
)
register_mcp_tools(mcp)
mcp_app = mcp.http_app(path="/mcp", stateless_http=True)


@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    async with service_lifespan(app):
        async with mcp_app.lifespan(app):
            yield


final_app = FastAPI(
    title="Email Server",
    description="Multi-user email service with OAuth-protected MCP",
    version="2.0.0",
    lifespan=combined_lifespan,
)
final_app.add_middleware(
    SessionMiddleware,
    secret_key=persistent_secret(settings.session_secret, "session.key"),
    https_only=settings.auth_mode == "google",
    same_site="lax",
)
final_app.mount("/api/v1", api_app)


@final_app.get("/")
async def root():
    return {
        "service": "Email Server",
        "version": "2.0.0",
        "auth_mode": settings.auth_mode,
        "apis": {"http": "/api/v1", "mcp": "/mcp", "health": "/api/v1/health"},
    }


@final_app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Minimal account console shell; the JSON API remains the authoritative interface."""
    login = ""
    if settings.auth_mode == "google":
        login = f"""
        <script src="https://accounts.google.com/gsi/client" async></script>
        <div id="g_id_onload" data-client_id="{settings.google_client_id}"
             data-callback="login"></div>
        <div class="g_id_signin" data-type="standard"></div>
        """
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Email accounts</title><style>
body{{font:14px system-ui;margin:0;color:#202124;background:#f7f8fa}}header{{background:#fff;border-bottom:1px solid #ddd;padding:16px 24px}}
main{{max-width:960px;margin:24px auto;padding:0 20px}}table{{width:100%;border-collapse:collapse;background:#fff}}
th,td{{padding:12px;text-align:left;border-bottom:1px solid #e5e7eb}}button,input,select{{font:inherit;padding:8px}}button{{cursor:pointer}}
#status{{margin:14px 0;color:#555}}form{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:16px 0 24px}}
label{{display:grid;gap:5px;color:#444}}.actions{{display:flex;gap:8px;align-items:center}}@media(max-width:640px){{form{{grid-template-columns:1fr}}}}</style></head>
<body><header><strong>Email accounts</strong></header><main>{login}<div id="status">Loading...</div>
<div class="actions"><a href="/api/v1/accounts/gmail/connect">Connect Gmail</a><button id="toggle" type="button">Add IMAP account</button></div>
<form id="add" hidden>
<label>Name<input name="name" required maxlength="255"></label><label>Email address<input name="account_name" type="email" required></label>
<label>Provider<select name="provider"><option value="imap">IMAP</option><option value="zoho">Zoho</option><option value="gmail">Gmail app password</option></select></label>
<label>Username<input name="username" required></label><label>IMAP host<input name="host" required></label>
<label>IMAP port<input name="port" type="number" value="993" required></label><label>SMTP host<input name="smtp_host" required></label>
<label>SMTP port<input name="smtp_port" type="number" value="465" required></label><label>Password<input name="password" type="password" required autocomplete="new-password"></label>
<label>SMTP mode<select name="smtp_mode"><option value="ssl">SSL</option><option value="starttls">STARTTLS</option></select></label>
<button type="submit">Add account</button></form>
<table><thead><tr><th>Name</th><th>Address</th><th>Provider</th><th>Last sync</th><th>Actions</th></tr></thead>
<tbody id="accounts"></tbody></table></main><script>
async function login(r){{await fetch('/api/v1/auth/google',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{credential:r.credential}})}});load()}}
async function load(){{let r=await fetch('/api/v1/accounts');if(!r.ok){{document.querySelector('#status').textContent='Sign in to manage accounts';return}}
let rows=await r.json();document.querySelector('#status').textContent=rows.length+' account(s)';let body=document.querySelector('#accounts');body.replaceChildren();
for(const a of rows){{let tr=document.createElement('tr');for(const value of [a.name,a.account_name||a.username,a.provider,a.last_check||'Never']){{let td=document.createElement('td');td.textContent=value;tr.appendChild(td)}}
let actions=document.createElement('td');for(const [label,path] of [['Test','test'],['Sync','sync']]){{let b=document.createElement('button');b.textContent=label;b.onclick=async()=>{{b.disabled=true;await fetch('/api/v1/accounts/'+a.id+'/'+path,{{method:'POST'}});b.disabled=false;load()}};actions.appendChild(b)}}tr.appendChild(actions);body.appendChild(tr)}}}}
document.querySelector('#toggle').onclick=()=>{{let form=document.querySelector('#add');form.hidden=!form.hidden}}
document.querySelector('#add').onsubmit=async e=>{{e.preventDefault();let form=e.currentTarget,data=Object.fromEntries(new FormData(form));let ssl=data.smtp_mode==='ssl';delete data.smtp_mode;
data.port=Number(data.port);data.smtp_port=Number(data.smtp_port);data.imap_use_ssl=true;data.imap_use_tls=false;data.smtp_use_ssl=ssl;data.smtp_use_tls=!ssl;
let r=await fetch('/api/v1/accounts',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(data)}});if(!r.ok){{document.querySelector('#status').textContent='Account could not be added';return}}
form.reset();form.hidden=true;load()}}
load()</script></body></html>"""
    )


# The MCP ASGI app owns /mcp and root-level OAuth discovery/callback routes.
final_app.mount("/", mcp_app)

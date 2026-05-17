#!/usr/bin/env python3
"""Gmail to ORGBRO X3 listener.

Listens for Gmail push notifications via Pub/Sub and prints each new email to the X3.
Mark as read after printing.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import signal
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.cloud import pubsub_v1
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
TOKEN_PATH = Path(__file__).parent.parent / "etc" / "gmail-token.json"
CREDS_PATH = Path(__file__).parent.parent / "etc" / "gmail-credentials.json"
STATE_PATH = Path(__file__).parent.parent / "etc" / "gmail_listener_state.json"
LABEL_PRINTED = "X3-Printed"
STOP_EVENT = asyncio.Event()

X3_APP_PYTHON = Path(__file__).parent.parent / "tools" / "X3Python.app" / "Contents" / "MacOS" / "X3Python"

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "gmail_listener.log"),
    ],
)
log = logging.getLogger("gmail_listener")


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"printed_ids": [], "first_run": datetime.now(timezone.utc).isoformat()}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


@dataclass
class Email:
    id: str
    thread_id: str
    sender: str
    date: datetime
    subject: str
    body: str


def load_credentials() -> Credentials:
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds


def decode_body(payload: dict) -> str:
    if "parts" in payload:
        for part in payload["parts"]:
            if part["mimeType"] == "text/plain" and "body" in part:
                data = part["body"].get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")
        for part in payload["parts"]:
            if part["mimeType"] == "text/html" and "body" in part:
                data = part["body"].get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")
    else:
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")
    return ""


def get_sender(headers: list[dict]) -> str:
    for h in headers:
        if h["name"].lower() == "from":
            return h["value"]
    return "Unknown"


def get_subject(headers: list[dict]) -> str:
    for h in headers:
        if h["name"].lower() == "subject":
            return h["value"]
    return "(no subject)"


def get_date(headers: list[dict]) -> datetime:
    for h in headers:
        if h["name"].lower() == "date":
            try:
                return parsedate_to_datetime(h["value"])
            except Exception:
                pass
    return datetime.now(timezone.utc)


def fetch_unread(service, label_ids: list[str]) -> list[Email]:
    query = "is:unread"
    results = (
        service.users()
        .messages()
        .list(userId="me", q=query, labelIds=label_ids if label_ids else None)
        .execute()
    )
    messages = results.get("messages", [])
    emails = []
    for msg in messages:
        msg_data = (
            service.users()
            .messages()
            .get(userId="me", id=msg["id"], format="full")
            .execute()
        )
        headers = msg_data.get("payload", {}).get("headers", [])
        sender = get_sender(headers)
        date = get_date(headers)
        subject = get_subject(headers)
        body = decode_body(msg_data.get("payload", {}))
        emails.append(
            Email(
                id=msg["id"],
                thread_id=msg_data.get("threadId", ""),
                sender=sender,
                date=date,
                subject=subject,
                body=body,
            )
        )
    return emails


def setup_watch(service, topic_path: str) -> dict:
    body = {"topicName": topic_path, "labelFilterAction": "INCLUDE"}
    return service.users().watch(userId="me", body=body).execute()


def create_label(service) -> str:
    try:
        label = (
            service.users()
            .labels()
            .create(userId="me", body={"name": LABEL_PRINTED, "labelListVisibility": "labelShow", "messageListVisibility": "show"})
            .execute()
        )
        return label["id"]
    except Exception:
        existing = service.users().labels().list(userId="me").execute()
        for lbl in existing.get("labels", []):
            if lbl["name"] == LABEL_PRINTED:
                return lbl["id"]
    return "INBOX"


def mark_read(service, msg_id: str) -> None:
    service.users().messages().modify(
        userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
    ).execute()


def mark_label(service, msg_id: str, label_id: str) -> None:
    service.users().messages().modify(
        userId="me", id=msg_id, body={"addLabelIds": [label_id]}
    ).execute()


# With Arial 48pt on 864 dots, each char averages ~26px wide → ~33 chars/line.
# Use 30 as conservative wrap width to avoid clipping.
WRAP_CHARS = 30
# Line height ≈ font_size * 1.3 (ascent + descent + leading)
LINE_HEIGHT_FACTOR = 1.3
PRINT_FONT_SIZE = 48
PRINT_WIDTH_DOTS = 864
# Minimum padding at the bottom so text isn't flush with the cut edge
HEIGHT_PAD_PX = 40


def format_for_printing(email: Email, max_chars: int = 500) -> str:
    date_str = email.date.strftime("%d/%m/%Y %H:%M")
    header = [
        f"Da: {email.sender}",
        f"Data: {date_str}",
        f"Oggetto: {email.subject}",
        "",
    ]
    body_raw = email.body[:max_chars].strip()
    if len(email.body) > max_chars:
        body_raw += "\n..."
    # Wrap every line (header + body) to fit the printer width
    wrapped: list[str] = []
    for line in header + body_raw.splitlines():
        if line == "":
            wrapped.append("")
        else:
            wrapped.extend(textwrap.wrap(line, width=WRAP_CHARS) or [""])
    return "\n".join(wrapped)


def _estimate_height(text: str) -> int:
    """Return the --height-rows needed to fit *text* at PRINT_FONT_SIZE."""
    num_lines = text.count("\n") + 1
    px = int(num_lines * PRINT_FONT_SIZE * LINE_HEIGHT_FACTOR) + HEIGHT_PAD_PX
    # Round up to multiple of 4 (rows_per_chunk default in q2_print_text)
    remainder = px % 4
    if remainder:
        px += 4 - remainder
    return px


def _print_executable() -> str:
    """Return the best Python executable for BLE printing.

    X3Python.app is a macOS app bundle with CoreBluetooth permissions in its
    Info.plist, so it won't crash when using bleak.  Fall back to the venv
    python if the app bundle is missing.
    """
    if X3_APP_PYTHON.exists():
        return str(X3_APP_PYTHON)
    log.warning("X3Python.app not found, falling back to sys.executable (BLE may crash)")
    return sys.executable


async def process_email(email: Email, service, label_id: str, printed_ids: set, state: dict) -> None:
    text = format_for_printing(email, 500)
    log.info("Printing email from %s | %s", email.sender, email.date.isoformat())
    ok = await print_email_background(text)
    if ok:
        mark_read(service, email.id)
        mark_label(service, email.id, label_id)
        printed_ids.add(email.id)
        state["printed_ids"] = list(printed_ids)
        save_state(state)
        log.info("Done. Email marked read and labeled.")
    else:
        log.error("Print failed, email left unread.")


async def print_email_background(text: str) -> bool:
    exe = _print_executable()
    script = str(Path(__file__).parent / "q2_print_text.py")
    # Set PYTHONPATH so X3Python.app can find venv packages
    env = {**__import__("os").environ}
    venv_site = Path(__file__).parent.parent / ".venv" / "lib"
    # Find the site-packages inside the venv
    site_dirs = list(venv_site.glob("python*/site-packages"))
    if site_dirs:
        env["PYTHONPATH"] = str(site_dirs[0])
    height = _estimate_height(text)
    cmd = [
        exe, script, text,
        "--width-dots", str(PRINT_WIDTH_DOTS),
        "--height-rows", str(height),
        "--font-size", str(PRINT_FONT_SIZE),
        "--feed-steps", "200",
        "--raw-chunk-size", "240",
        "--filter", "x3",
        "--align", "left",
        "--valign", "top",
    ]
    log.info("Running: %s", " ".join(cmd[:3]) + " ...")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.error("Print subprocess failed (rc=%d): %s", proc.returncode, stderr.decode(errors="replace")[:500])
    return proc.returncode == 0


def listen_pubsub(
    project: str,
    subscription: str,
    label_id: str,
    first_run: datetime,
    printed_ids: set,
    state: dict,
    loop: asyncio.AbstractEventLoop,
) -> None:
    subscriber = pubsub_v1.SubscriberClient()
    subscription_path = subscriber.subscription_path(project, subscription)

    def callback(message: pubsub_v1.subscriber.message.Message) -> None:
        """Runs in a Pub/Sub thread-pool thread — NOT in the asyncio loop."""
        try:
            data = json.loads(message.data.decode("utf-8"))
        except Exception:
            data = {}
        email_address = data.get("emailAddress", "unknown")
        history_id = data.get("historyId", "")
        log.info("Pub/Sub notification: %s, historyId=%s", email_address, history_id)

        # Build a fresh Gmail service *in this thread* to avoid SSL reuse issues
        try:
            service = build("gmail", "v1", credentials=load_credentials())
            emails = fetch_unread(service, [])
        except Exception as exc:
            log.error("Failed to fetch emails: %s", exc)
            message.ack()
            return

        new_emails = [e for e in emails if e.id not in printed_ids and e.date > first_run]
        if new_emails:
            for email in new_emails:
                try:
                    # Schedule the async work on the *main* event loop
                    future = asyncio.run_coroutine_threadsafe(
                        process_email(email, service, label_id, printed_ids, state),
                        loop,
                    )
                    future.result(timeout=120)  # block this thread until done
                except Exception as exc:
                    log.error("Print error: %s", exc)
        else:
            log.info("No new emails.")
        message.ack()

    streaming_pull = subscriber.subscribe(subscription_path, callback=callback)
    log.info("Listening on %s", subscription_path)
    log.info("Waiting for notifications... (Ctrl+C to stop)")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Gmail to ORGBRO X3 listener")
    parser.add_argument("--poll-interval", type=int, default=30, help="Seconds between inbox polls")
    parser.add_argument("--printer-filter", default="x3", help="BLE device filter for scanner")
    parser.add_argument("--printer-address", default=None, help="BLE address (skip scan)")
    parser.add_argument("--width-dots", type=int, default=864)
    parser.add_argument("--height-rows", type=int, default=180)
    parser.add_argument("--font-size", type=int, default=48)
    parser.add_argument("--feed-steps", type=int, default=200)
    parser.add_argument("--raw-chunk-size", type=int, default=240)
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument("--max-body-chars", type=int, default=500, help="Max body chars to print")
    parser.add_argument("--watch", action="store_true", help="Enable Gmail push notifications via Pub/Sub")
    parser.add_argument("--project-number", default="orgbro", help="GCP project name")
    parser.add_argument("--topic", default="gmail-notifications", help="Pub/Sub topic name")
    parser.add_argument("--subscription", default="gmail-listener-sub", help="Pub/Sub subscription name")
    parser.add_argument("--no-realtime", action="store_true", help="Disable Pub/Sub listener, use polling only")
    args = parser.parse_args()

    def stop_handler(signum, frame):
        log.info("Shutting down...")
        STOP_EVENT.set()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    creds = load_credentials()
    service = build("gmail", "v1", credentials=creds)
    label_id = create_label(service)
    state = load_state()
    first_run = datetime.fromisoformat(state["first_run"])
    printed_ids: set[str] = set(state["printed_ids"])

    if args.watch:
        topic_path = f"projects/{args.project_number}/topics/{args.topic}"
        log.info("Setting up Gmail watch on %s", topic_path)
        try:
            result = setup_watch(service, topic_path)
            log.info("Watch active: %s", result)
        except Exception as exc:
            log.error("Watch failed: %s", exc)
            args.watch = False

    log.info("Starting. Label: %s", LABEL_PRINTED)
    log.info("First run was at %s. Only emails after this will be printed.", first_run.isoformat())

    if args.watch and not args.no_realtime:
        loop = asyncio.get_running_loop()
        listen_pubsub(
            args.project_number, args.subscription, label_id,
            first_run, printed_ids, state, loop,
        )
        await asyncio.Event().wait()
    else:
        while not STOP_EVENT.is_set():
            try:
                # Rebuild service each iteration to avoid stale connections
                service = build("gmail", "v1", credentials=load_credentials())
                emails = fetch_unread(service, [])
                new_emails = [e for e in emails if e.id not in printed_ids and e.date > first_run]
                if new_emails:
                    for email in new_emails:
                        await process_email(email, service, label_id, printed_ids, state)
                else:
                    log.info("No new emails.")
            except Exception as exc:
                log.error("Error: %s", exc)
            await asyncio.sleep(args.poll_interval)


if __name__ == "__main__":
    asyncio.run(main())
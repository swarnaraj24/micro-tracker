import os, re, base64, html, datetime
from zoneinfo import ZoneInfo
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from supabase import create_client

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
IST = ZoneInfo("Asia/Kolkata")

# Sender allowlist. Confirm the exact addresses from your inbox and edit if needed.
SENDERS = os.environ.get("TX_SENDERS", "hdfcbank icicibank").split()
SINCE = os.environ.get("TX_SINCE", "2026/09/30")
MAX_MESSAGES = int(os.environ.get("TX_MAX", "1000"))

AMOUNT = re.compile(
    r'(?:Rs\.?|INR|\u20b9)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)(?!\s*(?:cr|crore|lakh|l|k)\b)',
    re.I)
DEBIT = re.compile(r'\b(debited|spent|paid|sent|withdrawn|purchase)\b', re.I)
CREDIT = re.compile(r'\b(credited|received|deposited|refund(?:ed)?)\b', re.I)
SKIP = re.compile(r'\b(otp|one[- ]time password|pre-approved|statement is ready|offer)\b', re.I)
VPA = re.compile(r'\b([a-z0-9.\-_]+@[a-z0-9]+)(?![a-z0-9]|\.[a-z])', re.I)
ICICI_PAYEE = re.compile(r';\s*([A-Za-z][A-Za-z0-9 &.\-]{1,40}?)\s+(?:credited|debited)\b', re.I)
PAYEE = re.compile(r'\b(?:to|at|towards)\s+([A-Za-z][A-Za-z0-9 &.\-]{2,35}?)(?:\s+(?:on|via|ref|using)\b|[.,;(]|$)')


def get_service():
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    return build('gmail', 'v1', credentials=creds)


def walk(part, out):
    """Collect text from every nested MIME part. HTML is stripped to plain text."""
    data = part.get('body', {}).get('data')
    mime = part.get('mimeType', '')
    if data and mime in ('text/plain', 'text/html'):
        raw = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
        if mime == 'text/html':
            raw = html.unescape(re.sub(r'<(style|script).*?</\1>|<[^>]+>', ' ', raw, flags=re.S | re.I))
        out.append(re.sub(r'\s+', ' ', raw).strip())
    for p in part.get('parts', []):
        walk(p, out)


def tx_date(ms):
    return datetime.datetime.fromtimestamp(int(ms) / 1000, IST).strftime('%Y-%m-%d')


def describe(text):
    m = ICICI_PAYEE.search(text)
    if m:
        return m.group(1).strip().title()
    m = VPA.search(text)
    if m:
        return f"UPI: {m.group(1).lower()}"
    m = PAYEE.search(text)
    if m:
        return m.group(1).strip().title()
    return "Bank transaction"


def parse(subject, body, internal_ms):
    text = f"{subject} {body}"
    if SKIP.search(text):
        return None
    amt = AMOUNT.search(text)
    if not amt:
        return None
    debit, credit = DEBIT.search(text), CREDIT.search(text)
    if not (debit or credit):
        return None
    # Whichever direction word appears first in the text wins.
    is_income = bool(credit) and (not debit or credit.start() < debit.start())
    low = text.lower()
    mode = 'HDFC Bank' if 'hdfc' in low else 'ICICI Bank' if 'icici' in low else 'UPI'
    tx_type = 'income' if is_income else 'expense'
    if 'credit card' in low and re.search(r'payment (?:received|of)', low):
        tx_type, mode = 'bill_payment', 'CC Bill Payment'
    return {
        "amount": round(float(amt.group(1).replace(',', '')), 2),
        "payment_mode": mode,
        "type": tx_type,
        "description": describe(body or subject),
        "date": tx_date(internal_ms),
    }


def list_message_ids(svc, query):
    """Page through Gmail results until MAX_MESSAGES is reached."""
    ids, token = [], None
    while len(ids) < MAX_MESSAGES:
        res = svc.users().messages().list(
            userId='me', q=query, maxResults=100, pageToken=token).execute()
        ids.extend(m['id'] for m in res.get('messages', []))
        token = res.get('nextPageToken')
        if not token:
            break
    return ids[:MAX_MESSAGES]


def sync():
    svc = get_service()
    query = f"from:({' OR '.join(SENDERS)}) after:{SINCE}"
    ids = list_message_ids(svc, query)
    print(f"Scanning {len(ids)} messages")
    logged = skipped = 0
    for msg_id in ids:
        try:
            if supabase.table('transactions').select('id').eq('email_id', msg_id).execute().data:
                continue
            msg = svc.users().messages().get(userId='me', id=msg_id, format='full').execute()
            headers = msg['payload'].get('headers', [])
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
            parts = []
            walk(msg['payload'], parts)
            body = ' '.join(parts) or msg.get('snippet', '')
            tx = parse(subject, body, msg.get('internalDate', 0))
            if not tx:
                skipped += 1
                continue
            supabase.table('transactions').insert(
                {"email_id": msg_id, "source": "email", **tx}).execute()
            print(f"LOGGED {tx['date']} Rs {tx['amount']} {tx['type']} {tx['description']}")
            logged += 1
        except Exception as e:
            print(f"Error on {msg_id}: {e}")
    print(f"Done. Logged {logged} new transactions, ignored {skipped} non-transaction emails.")


if __name__ == '__main__':
    sync()

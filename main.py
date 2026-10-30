import os
import re
import base64
from datetime import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from supabase import create_client, Client

# Supabase Configuration using environment variables
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Gmail API Scopes
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def get_gmail_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)

def parse_transaction_email(msg_id, internal_date_ms, subject, body_text):
    full_text = (subject + " " + body_text).lower()
    
    # Filter out promotional / marketing emails completely
    if any(keyword in full_text for keyword in ["offer", "discount", "cashback", "reward", "jumbo loan", "pre-approved", "upgrade"]):
        return None

    # Search for amount in the full body text
    amount_match = re.search(r'(?i)(?:rs\.?|inr|₹)\s*([0-9,]+\.?[0-9]*)', body_text)
    if not amount_match:
        return None
    
    amount_str = amount_match.group(1).replace(',', '')
    try:
        amount = float(amount_str)
    except ValueError:
        return None

    # Determine transaction type (Expense vs Income)
    if any(word in full_text for word in ["debited", "spent", "paid", "sent", "dr", "purchase"]):
        tx_type = "expense"
    elif any(word in full_text for word in ["credited", "received", "added", "cr", "cashback received"]):
        tx_type = "income"
    else:
        return None

    # Determine payment mode dynamically
    if "upi" in full_text or "@" in full_text:
        payment_mode = "UPI"
    elif "card" in full_text or "cc" in full_text or "dc" in full_text:
        payment_mode = "Card"
    elif "imps" in full_text or "neft" in full_text or "rtgs" in full_text:
        payment_mode = "Bank Transfer"
    else:
        payment_mode = "Other"

    tx_date = datetime.fromtimestamp(int(internal_date_ms) / 1000.0).strftime('%Y-%m-%d')

    return {
        "email_id": msg_id,
        "amount": amount,
        "payment_mode": payment_mode,
        "type": tx_type,
        "description": body_text.strip()[:150],
        "date": tx_date
    }

def push_to_supabase(data):
    response = supabase.table("transactions").insert(data).execute()
    return response

def main():
    service = get_gmail_service()
    
    # Query for bank emails starting from Sep 30, 2026 onwards
    query = 'from:(hdfc OR icici) after:2026/09/30'
    
    results = service.users().messages().list(userId='me', q=query).execute()
    messages = results.get('messages', [])

    if not messages:
        print("No transactions found matching the query.")
        return

    print(f"Found {len(messages)} messages to process.")

    for msg in messages:
        msg_data = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
        internal_date = msg_data.get('internalDate', '')
        
        headers = msg_data.get('payload', {}).get('headers', [])
        subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
        
        body_text = ""
        payload = msg_data.get('payload', {})
        if 'body' in payload and 'data' in payload['body']:
            body_text = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='ignore')
        elif 'parts' in payload:
            for part in payload['parts']:
                if part.get('mimeType') == 'text/plain' and 'data' in part.get('body', {}):
                    body_text = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='ignore')
                    break
        
        if not body_text:
            body_text = msg_data.get('snippet', '')

        parsed_data = parse_transaction_email(msg['id'], internal_date, subject, body_text)
        if parsed_data:
            try:
                push_to_supabase(parsed_data)
                print(f"Pushed Rs. {parsed_data['amount']} ({parsed_data['type']} - {parsed_data['payment_mode']}) on {parsed_data['date']} -> Success")
            except Exception as e:
                print(f"Skipping message ID {msg['id']} (likely duplicate): {e}")

if __name__ == '__main__':
    main()

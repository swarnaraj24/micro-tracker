import os
import re
from datetime import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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

def parse_transaction_email(msg_id, internal_date_ms, snippet):
    # Flexible regex to catch amounts following Rs, INR, or ₹
    amount_match = re.search(r'(?i)(?:rs\.?|inr|₹)\s*([0-9,]+\.?[0-9]*)', snippet)
    if not amount_match:
        return None
    
    amount_str = amount_match.group(1).replace(',', '')
    try:
        amount = float(amount_str)
    except ValueError:
        return None

    tx_type = "expense" if re.search(r'(?i)debited|spent|paid', snippet) else "income"

    # Convert Gmail's millisecond timestamp into a real YYYY-MM-DD date string
    tx_date = datetime.fromtimestamp(int(internal_date_ms) / 1000.0).strftime('%Y-%m-%d')

    return {
        "email_id": msg_id,
        "amount": amount,
        "payment_mode": "UPI",
        "type": tx_type,
        "description": snippet[:100],
        "date": tx_date  # Actual transaction date extracted from email metadata
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
        # Fetch full message including internalDate
        msg_data = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
        snippet = msg_data.get('snippet', '')
        internal_date = msg_data.get('internalDate', '')
        
        parsed_data = parse_transaction_email(msg['id'], internal_date, snippet)
        if parsed_data:
            try:
                push_to_supabase(parsed_data)
                print(f"Pushed transaction Rs. {parsed_data['amount']} on {parsed_data['date']} -> Supabase Success")
            except Exception as e:
                print(f"Skipping message ID {msg['id']} (likely duplicate): {e}")

if __name__ == '__main__':
    main()

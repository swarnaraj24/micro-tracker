import os
import re
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def get_gmail_service():
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    return build('gmail', 'v1', credentials=creds)

def parse_transaction(snippet):
    # Flexible amount matcher: looks for currency or standard number patterns
    amount_match = re.search(r'(?:Rs\.?|INR|₹|HDFC|ICICI|SBI|Axis|Paytm|PhonePe|GPay)?\s*([\d,]+\.\d{2})', snippet, re.IGNORECASE)
    if not amount_match:
        # Fallback regex for standard amounts
        amount_match = re.search(r'(?:Rs\.?|INR|₹)\s*([\d,]+)', snippet, re.IGNORECASE)
        
    if not amount_match:
        return None
    
    try:
        amount = float(amount_match.group(1).replace(',', ''))
    except ValueError:
        return None

    lower_snippet = snippet.lower()
    
    if 'credit card' in lower_snippet or 'ending xx' in lower_snippet or 'card' in lower_snippet:
        mode = 'Credit Card'
    else:
        mode = 'UPI / Debit'
        
    tx_type = 'income' if 'credited' in lower_snippet or 'received' in lower_snippet or 'added' in lower_snippet else 'expense'
        
    return {
        "amount": amount,
        "payment_mode": mode,
        "type": tx_type,
        "description": snippet[:100]
    }

def sync_transactions():
    service = get_gmail_service()
    query = 'newer_than:30d (debited OR spent OR paid OR credited OR received OR alert OR transaction OR INR OR Rs)'
    results = service.users().messages().list(userId='me', q=query, maxResults=10).execute()
    messages = results.get('messages', [])

    print(f"Raw Gmail search results keys: {results.keys()}")
    print(f"Messages list type: {type(messages)}, length: {len(messages)}")

    if not messages:
        print("No messages found in list.")
        return

    for i, msg in enumerate(messages):
        msg_id = msg['id']
        print(f"Fetching message {i+1} ID: {msg_id}")
        
        message = service.users().messages().get(userId='me', id=msg_id).execute()
        snippet = message.get('snippet', '')
        print(f"Snippet found: {snippet[:80]}")
        
        tx = parse_transaction(snippet)
        if tx:
            row = {
                "email_id": msg_id,
                "amount": tx["amount"],
                "payment_mode": tx["payment_mode"],
                "type": tx["type"],
                "description": tx["description"]
            }
            supabase.table('transactions').insert(row).execute()
            print(f"--> SUCCESS LOGGED [{tx['type']}]: ₹{tx['amount']} via {tx['payment_mode']}")
        else:
            print(f"--> SKIPPED (No regex match)")

if __name__ == '__main__':
    sync_transactions()

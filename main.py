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
    amount_match = re.search(r'(?:Rs\.?|INR|₹)\s*([\d,]+\.?\d*)', snippet, re.IGNORECASE)
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
        "amount": round(amount, 2),
        "payment_mode": mode,
        "type": tx_type,
        "description": str(snippet[:100])
    }

def sync_transactions():
    service = get_gmail_service()
    query = 'newer_than:30d (debited OR spent OR paid OR credited OR received OR UPI OR VPA OR PayTM OR PhonePe OR GooglePay OR HDFC OR SBI OR ICICI OR Axis)'
    results = service.users().messages().list(userId='me', q=query, maxResults=30).execute()
    messages = results.get('messages', [])

    print(f"Found {len(messages)} targeted financial messages.")

    if not messages:
        print("No messages found.")
        return

    for msg in messages:
        msg_id = msg['id']
        
        try:
            existing = supabase.table('transactions').select('id').eq('email_id', msg_id).execute()
            if existing.data:
                continue

            message = service.users().messages().get(userId='me', id=msg_id).execute()
            snippet = message.get('snippet', '')
            
            tx = parse_transaction(snippet)
            if tx:
                row = {
                    "email_id": str(msg_id),
                    "amount": float(tx["amount"]),
                    "payment_mode": str(tx["payment_mode"]),
                    "type": str(tx["type"]),
                    "description": str(tx["description"])
                }
                supabase.table('transactions').insert(row).execute()
                print(f"SUCCESS LOGGED [{tx['type']}]: ₹{tx['amount']} via {tx['payment_mode']}")
        except Exception as e:
            print(f"Skipping message due to error: {e}")

if __name__ == '__main__':
    sync_transactions()

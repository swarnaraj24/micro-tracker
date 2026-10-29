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
    lower_snippet = snippet.lower()
    
    # 1. Look for transaction indicators to ensure it's a financial alert
    if not any(keyword in lower_snippet for keyword in ['debited', 'spent', 'paid', 'credited', 'received', 'sent', 'transferred', 'txn', 'upi']):
        return None

    # 2. Flexible amount extractor: finds numbers near currency or standard transaction phrasing
    # Matches formats like: 500, 1,250.00, 45.50
    amounts = re.findall(r'(?:Rs\.?|INR|₹)?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?|[0-9]+(?:\.[0-9]{2})?)', snippet)
    
    if not amounts:
        return None
    
    # Take the last or largest reasonable numeric match as the transaction amount
    valid_amounts = []
    for a in amounts:
        try:
            val = float(a.replace(',', ''))
            if val > 0:
                valid_amounts.append(val)
        except ValueError:
            continue
            
    if not valid_amounts:
        return None
        
    amount = max(valid_amounts) # Usually the total transaction amount is the prominent figure

    if 'credit card' in lower_snippet or 'ending' in lower_snippet or 'card' in lower_snippet:
        mode = 'Credit Card'
    else:
        mode = 'UPI / Debit'
        
    tx_type = 'income' if any(w in lower_snippet for w in ['credited', 'received', 'added', 'refund']) else 'expense'
        
    return {
        "amount": round(amount, 2),
        "payment_mode": mode,
        "type": tx_type,
        "description": str(snippet[:100])
    }

def sync_transactions():
    service = get_gmail_service()
    query = 'newer_than:30d (debited OR spent OR paid OR credited OR received OR UPI OR PayTM OR PhonePe OR GooglePay OR HDFC OR SBI OR ICICI OR Axis OR Bank)'
    results = service.users().messages().list(userId='me', q=query, maxResults=50).execute()
    messages = results.get('messages', [])

    print(f"Scanning {len(messages)} messages for transactions...")

    if not messages:
        print("No messages found.")
        return

    logged_count = 0
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
                logged_count += 1
        except Exception as e:
            print(f"Skipping message due to error: {e}")
            
    print(f"Sync complete. Successfully logged {logged_count} new transactions.")

if __name__ == '__main__':
    sync_transactions()

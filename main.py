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
    service = build('gmail', 'v1', credentials=creds)
    
    # Print the exact Gmail account being accessed
    try:
        profile = service.users().getProfile(userId='me').execute()
        print(f"=== CONNECTED GMAIL ACCOUNT: {profile.get('emailAddress')} ===")
    except Exception as e:
        print(f"Could not fetch profile email: {e}")
        
    return service

def parse_transaction(snippet, subject):
    combined_text = f"{subject} {snippet}"
    lower_text = combined_text.lower()
    
    # Must contain a financial keyword
    if not any(keyword in lower_text for keyword in ['debited', 'spent', 'paid', 'credited', 'received', 'sent', 'transferred', 'txn', 'upi', 'rs', 'inr']):
        return None

    # Extract amount
    amounts = re.findall(r'(?:Rs\.?|INR|₹)?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?|[0-9]+(?:\.[0-9]{2})?)', combined_text)
    
    if not amounts:
        return None
    
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
        
    amount = max(valid_amounts)

    if 'credit card' in lower_text or 'ending' in lower_text or 'card' in lower_text:
        mode = 'Credit Card'
    else:
        mode = 'UPI / Debit'
        
    tx_type = 'income' if any(w in lower_text for w in ['credited', 'received', 'added', 'refund']) else 'expense'
        
    return {
        "amount": round(amount, 2),
        "payment_mode": mode,
        "type": tx_type,
        "description": str(combined_text[:100])
    }

def sync_transactions():
    service = get_gmail_service()
    
    # Broad query to catch financial alerts
    query = 'newer_than:30d (debited OR spent OR paid OR credited OR received OR UPI OR PayTM OR PhonePe OR GooglePay OR HDFC OR SBI OR ICICI OR Axis OR Bank OR Rs OR INR)'
    results = service.users().messages().list(userId='me', q=query, maxResults=30).execute()
    messages = results.get('messages', [])

    print(f"Scanning {len(messages)} messages...")

    if not messages:
        print("No messages found matching query.")
        return

    logged_count = 0
    for msg in messages:
        msg_id = msg['id']
        
        try:
            existing = supabase.table('transactions').select('id').eq('email_id', msg_id).execute()
            if existing.data:
                continue

            message = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
            
            # Extract subject header
            headers = message.get('payload', {}).get('headers', [])
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
            
            snippet = message.get('snippet', '')
            
            print(f"Checking Email Subject: {subject[:60]}")
            
            tx = parse_transaction(snippet, subject)
            if tx:
                row = {
                    "email_id": str(msg_id),
                    "amount": float(tx["amount"]),
                    "payment_mode": str(tx["payment_mode"]),
                    "type": str(tx["type"]),
                    "description": str(tx["description"])
                }
                supabase.table('transactions').insert(row).execute()
                print(f"--> SUCCESS LOGGED [{tx['type']}]: ₹{tx['amount']} via {tx['payment_mode']}")
                logged_count += 1
        except Exception as e:
            print(f"Skipping message due to error: {e}")
            
    print(f"Sync complete. Successfully logged {logged_count} new transactions.")

if __name__ == '__main__':
    sync_transactions()

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
    try:
        profile = service.users().getProfile(userId='me').execute()
        print(f"=== CONNECTED GMAIL ACCOUNT: {profile.get('emailAddress')} ===")
    except Exception as e:
        print(f"Could not fetch profile email: {e}")
    return service

def parse_transaction(snippet, subject, body=""):
    combined_text = f"{subject} {snippet} {body}"
    lower_text = combined_text.lower()
    
    # Check for transaction keywords including ICICI and HDFC specific phrasing
    if not any(keyword in lower_text for keyword in ['debited', 'spent', 'paid', 'credited', 'received', 'sent', 'transferred', 'txn', 'upi', 'rs', 'inr', 'used for a transaction']):
        return None

    # Extract amount (handles formats like INR 1,234.56, Rs.298.00, or ₹1234)
    amounts = re.findall(r'(?:Rs\.?|INR|₹)\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?|[0-9]+(?:\.[0-9]{2})?)', combined_text)
    
    # Fallback generic numbers if currency prefix is missing
    if not amounts:
        amounts = re.findall(r'\b([0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{2}|[0-9]+\.[0-9]{2})\b', combined_text)

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

    if 'credit card' in lower_text or 'icici' in lower_text or 'hdfc' in lower_text or 'ending' in lower_text:
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
    
    # Broad query covering ICICI, HDFC, and UPI transaction alerts
    query = 'newer_than:30d (debited OR spent OR paid OR credited OR received OR UPI OR PayTM OR PhonePe OR GooglePay OR HDFC OR SBI OR ICICI OR Bank OR Rs OR INR OR "used for a transaction")'
    results = service.users().messages().list(userId='me', q=query, maxResults=50).execute()
    messages = results.get('messages', [])

    print(f"Scanning {len(messages)} messages...")

    if not messages:
        print("No messages found matching query.")
        return

    logged_count = 0
    for msg in messages:
        msg_id = msg['id']
        
        try:
            message = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
            headers = message.get('payload', {}).get('headers', [])
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
            snippet = message.get('snippet', '')
            
            print(f"Checking subject: {subject}")
            
            existing = supabase.table('transactions').select('id').eq('email_id', msg_id).execute()
            if existing.data:
                print(f" -> Already logged, skipping.")
                continue

            # Extract full body
            body = ""
            payload = message.get('payload', {})
            if 'parts' in payload:
                for part in payload['parts']:
                    if part.get('mimeType') == 'text/plain':
                        data = part.get('body', {}).get('data', '')
                        if data:
                            import base64
                            body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                            break

            tx = parse_transaction(snippet, subject, body)
            if tx:
                row = {
                    "email_id": str(msg_id),
                    "amount": float(tx["amount"]),
                    "payment_mode": str(tx["payment_mode"]),
                    "type": str(tx["type"]),
                    "description": str(tx["description"])
                }
                
                # Try inserting and catch any database issues explicitly
                res = supabase.table('transactions').insert(row).execute()
                print(f"--> SUCCESS LOGGED [{tx['type']}]: ₹{tx['amount']} via {tx['payment_mode']}")
                logged_count += 1
            else:
                print(f" -> Regex/Keywords did not match snippet/body.")
        except Exception as e:
            print(f"ERROR processing message {msg_id}: {e}")
            
    print(f"Sync complete. Successfully logged {logged_count} new transactions.")

if __name__ == '__main__':
    sync_transactions()

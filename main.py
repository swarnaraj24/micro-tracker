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

def purge_unwanted_records():
    """Removes previously logged junk/promotional entries from Supabase."""
    junk_patterns = [
        'offer', 'limit', 'loan', 'policy', 'gtt', 'cuspa', 'reward', 
        'cashback', 'upgrade', 'pre-approved', 'karwa chauth', 'diwali', 
        'bonus', 'charges', 'smartemi', 'registration', 'welcome'
    ]
    for pattern in junk_patterns:
        try:
            supabase.table('transactions').delete().ilike('description', f'%{pattern}%').execute()
        except Exception as e:
            print(f"Purge note for {pattern}: {e}")

def parse_transaction(snippet, subject, body=""):
    combined_text = f"{subject} {snippet} {body}"
    lower_text = combined_text.lower()
    
    # 1. HARD BLOCK: If any promotional or marketing words are present, drop it immediately.
    blocklist = [
        'offer', 'limit', 'loan', 'policy', 'gtt', 'cuspa', 'reward', 'cashback', 
        'upgrade', 'pre-approved', 'diwali', 'karwa chauth', 'bonus', 'charges', 
        'smartemi', 'registration', 'welcome', 'save up to', 'health benefits', 
        'interest rate starts'
    ]
    if any(b in lower_text for b in blocklist):
        return None

    # 2. REQUIRED: Must contain explicit action words confirming money actually moved.
    action_keywords = ['debited', 'spent', 'paid', 'credited', 'received', 'sent', 'transferred']
    if not any(act in lower_text for act in action_keywords):
        return None

    # Extract amount safely
    amounts = re.findall(r'(?:Rs\.?|INR|₹)\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?|[0-9]+(?:\.[0-9]{2})?)', combined_text)
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

    # Filter out unrealistically massive numbers accidentally picked up (like 25,000 account charges)
    if amount > 200000 and 'salary' not in lower_text:
        return None

    # Accurate Payment Mode Detection
    if 'icici' in lower_text:
        mode = 'ICICI Bank'
    elif 'hdfc' in lower_text:
        mode = 'HDFC Bank'
    elif 'rupay' in lower_text:
        mode = 'RuPay Credit Card'
    elif 'upi' in lower_text or 'vpa' in lower_text or '@' in lower_text:
        mode = 'UPI'
    else:
        mode = 'UPI / Card'
        
    # Income vs Expense classification
    tx_type = 'income' if any(w in lower_text for w in ['credited', 'received', 'salary', 'added', 'refund']) else 'expense'
        
    return {
        "amount": round(amount, 2),
        "payment_mode": mode,
        "type": tx_type,
        "description": str(subject[:100])
    }

def sync_transactions():
    print("Starting clean sync workflow...")
    
    # Automatically clean out past junk rows from Supabase
    purge_unwanted_records()
    
    service = get_gmail_service()
    
    # Targeted query focusing purely on actual financial movement
    query = 'newer_than:7d (debited OR spent OR paid OR credited OR received OR UPI OR ICICI OR HDFC)'
    results = service.users().messages().list(userId='me', q=query, maxResults=50).execute()
    messages = results.get('messages', [])

    print(f"Scanning {len(messages)} messages...")
    logged_count = 0

    for msg in messages:
        msg_id = msg['id']
        try:
            existing = supabase.table('transactions').select('id').eq('email_id', msg_id).execute()
            if existing.data:
                continue

            message = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
            headers = message.get('payload', {}).get('headers', [])
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
            snippet = message.get('snippet', '')
            
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
                supabase.table('transactions').insert(row).execute()
                print(f"--> LOGGED [{tx['type']}]: ₹{tx['amount']} via {tx['payment_mode']}")
                logged_count += 1
        except Exception as e:
            print(f"Error: {e}")
            
    print(f"Sync complete. Logged {logged_count} new transactions.")

if __name__ == '__main__':
    sync_transactions()

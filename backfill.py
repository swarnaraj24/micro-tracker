import sys
from email_parser import supabase, get_service, walk, parse, SENDERS

APPLY = '--apply' in sys.argv
svc = get_service()
rows = supabase.table('transactions').select('*').not_.is_('email_id', 'null').range(0, 999).execute().data
print(f"Checking {len(rows)} rows ({'APPLY' if APPLY else 'DRY RUN'})")

for r in rows:
    try:
        msg = svc.users().messages().get(userId='me', id=r['email_id'], format='full').execute()
    except Exception as e:
        print(f"SKIP {r['email_id']}: {e}")
        continue
    hdr = {h['name'].lower(): h['value'] for h in msg['payload'].get('headers', [])}
    sender, subject = hdr.get('from', '').lower(), hdr.get('subject', '')
    parts = []
    walk(msg['payload'], parts)
    body = ' '.join(parts) or msg.get('snippet', '')
    tx = parse(subject, body, msg.get('internalDate', 0)) if any(s in sender for s in SENDERS) else None

    if tx is None:
        print(f"DELETE  {r['date']} {r['amount']} | {str(r['description'])[:60]}")
        if APPLY:
            supabase.table('transactions').delete().eq('id', r['id']).execute()
        continue

    changes = {k: v for k, v in tx.items() if str(r.get(k)) != str(v)}
    if changes:
        print(f"UPDATE  {r['date']} {r['amount']} | {str(r['description'])[:40]} -> {changes}")
        if APPLY:
            supabase.table('transactions').update(changes).eq('id', r['id']).execute()
print("Done")

import psycopg2
from urllib.parse import quote_plus

password = "35946800Pe@"
encoded_pass = quote_plus(password)

dsn = f"postgresql://postgres:{encoded_pass}@db.ooomoyseycfoctdcsbdx.supabase.co:5432/postgres"

print("Testing direct database connection to Supabase...")
try:
    c = psycopg2.connect(dsn, connect_timeout=5)
    print("SUCCESS!")
    c.close()
except Exception as e:
    print(f"FAILED: {e}")

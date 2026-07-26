"""
BIR KERELIK KULLANIM - Bu scripti kendi bilgisayarinda calistir.

Ne yapar: Tarayicinda YouTube hesabina "bu uygulamaya izin veriyorum" onayi
sordurur, sonra sana bir REFRESH TOKEN verir. Bu token'i GitHub Secrets'a
(YT_REFRESH_TOKEN olarak) ekleyeceksin - ondan sonra bir daha bu scripti
calistirmana gerek kalmaz, GitHub Actions o token ile sinirsiz/headless
YouTube'a yukleme yapabilir.

Kurulum (bilgisayarinda, bir kerelik):
    pip install google-auth-oauthlib

Kullanim:
    python get_refresh_token.py client_secret.json

(client_secret.json = Google Cloud Console'dan indirdigin OAuth dosyasi)
"""

import sys
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main():
    if len(sys.argv) < 2:
        print("Kullanım: python get_refresh_token.py client_secret.json")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
    creds = flow.run_local_server(port=0)

    print("\n\n=== BASARILI ===")
    print("Asagidaki 3 degeri GitHub Secrets'a ekle:\n")
    print(f"YT_CLIENT_ID = {creds.client_id}")
    print(f"YT_CLIENT_SECRET = {creds.client_secret}")
    print(f"YT_REFRESH_TOKEN = {creds.refresh_token}")


if __name__ == "__main__":
    main()

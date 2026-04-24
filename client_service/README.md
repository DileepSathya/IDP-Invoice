## IDP Client Service (Python)

This is a small reference client that can:

- Receive IDP webhooks with signature verification
- Upload one or more files concurrently to the IDP `/v1/files` endpoint

### Install

```bash
cd client_service
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
```

### Run webhook receiver

Set the same secret you will pass as `callback_secret` during upload:

```bash
set WEBHOOK_SECRET=your-shared-secret
uvicorn webhook_receiver:app --host 0.0.0.0 --port 9000
```

Your callback URL (from the IDP server point of view) will be:

- `http://<client-ip>:9000/idp/webhook` (LAN)

### Upload files (with webhook)

```bash
set API_BASE=http://192.168.29.237:8000
set API_KEY=test-key-123
set CALLBACK_URL=http://<client-ip>:9000/idp/webhook
set WEBHOOK_SECRET=your-shared-secret

python uploader.py D:\\IDP_2.0-INVOICE\\DATA\\SD-6.jpeg D:\\IDP_2.0-INVOICE\\DATA\\SD-7.jpeg --concurrency 5
```

### Upload files (no webhook)

```bash
set API_BASE=http://192.168.29.237:8000
set API_KEY=test-key-123

python uploader.py D:\\IDP_2.0-INVOICE\\DATA\\SD-6.jpeg --concurrency 5
```


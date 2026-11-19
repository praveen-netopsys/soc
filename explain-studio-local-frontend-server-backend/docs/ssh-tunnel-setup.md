# Windows SSH Tunnel Setup Guide for Explain Studio

This guide explains how to establish a secure, private SSH tunnel between your Windows development laptop and the Ubuntu company server hosting Ollama.

---

## 1. Architecture Overview

```
+-------------------------------------------------------------+
|                 Windows Development Laptop                   |
|                                                             |
|  [ React Frontend ]  ---> [ FastAPI Backend ]               |
|   http://127.0.0.1:5173     http://127.0.0.1:8000           |
|                                     |                       |
|                                     v                       |
|                          localhost:11434 (Loopback)         |
+-------------------------------------|-----------------------+
                                      |
                         Encrypted SSH Tunnel
                         (Port Forwarding -L)
                                      |
+-------------------------------------v-----------------------+
|                 Ubuntu Company AI Server                     |
|                                                             |
|                          localhost:11434                    |
|                                 |                           |
|                                 v                           |
|                        [ Ollama Service ]                   |
|                        - qwen2.5:14b                        |
|                        - qwen2.5vl:7b                       |
+-------------------------------------------------------------+
```

### Safety Principles
1. **No Public Exposure**: Ollama is bound exclusively to `127.0.0.1:11434` on the company server. It is **never** bound to `0.0.0.0` or exposed over public IP/ports.
2. **Encrypted Transport**: All communication passes through an authenticated SSH tunnel using your company credentials or private key.
3. **No Automatic Pulling**: Explain Studio will never automatically pull or download models (`ollama pull` is disabled).
4. **Graceful Fallback**: If the SSH tunnel is disconnected, the Explain Studio backend and frontend gracefully report `Ollama Offline` without crashing or blocking file extraction workflows.

---

## 2. Quick Start: Single PowerShell Command

Open PowerShell on your Windows laptop and run:

```powershell
# Forward Ollama (port 11434) from company server to Windows localhost
ssh -N -L 11434:127.0.0.1:11434 <YOUR_USERNAME>@<COMPANY_SERVER_IP>
```

### Flags Explanation:
- `-N`: Do not execute remote commands (tunnelling only, keeps connection idle).
- `-L 11434:127.0.0.1:11434`: Forwards local port `11434` on `127.0.0.1` to remote `127.0.0.1:11434`.

> **Note:** If you also run the FastAPI backend directly on the remote server rather than locally, forward both ports:
> ```powershell
> ssh -N -L 8000:127.0.0.1:8000 -L 11434:127.0.0.1:11434 <YOUR_USERNAME>@<COMPANY_SERVER_IP>
> ```

---

## 3. Recommended: Persistent SSH Config (`~/.ssh/config`)

To avoid typing the full command every time and ensure automatic keepalive, configure your OpenSSH client profile.

1. Create or edit `C:\Users\<YourUsername>\.ssh\config`:
```ssh-config
Host explain-studio-ai
    HostName <COMPANY_SERVER_IP_OR_HOSTNAME>
    User <YOUR_USERNAME>
    IdentityFile ~/.ssh/id_ed25519
    LocalForward 11434 127.0.0.1:11434
    # Keepalive to prevent drops through idle NAT firewalls:
    ServerAliveInterval 60
    ServerAliveCountMax 3
    # Optional compression
    Compression yes
```

2. To open the tunnel, run:
```powershell
ssh -N explain-studio-ai
```

---

## 4. Verification Steps

### Step 1: Verify Raw Ollama Connectivity from Windows
In a separate PowerShell window:

```powershell
curl.exe http://127.0.0.1:11434/
```
Expected output:
```text
Ollama is running
```

List installed models directly from Ollama:
```powershell
curl.exe http://127.0.0.1:11434/api/tags
```

### Step 2: Verify Explain Studio AI Health Endpoint
```powershell
curl.exe http://127.0.0.1:8000/v1/ai/health
```
Expected output:
```json
{
  "status": "connected",
  "connected": true,
  "base_url": "http://127.0.0.1:11434",
  "latency_ms": 15.2,
  "tunnel_active": true,
  "error": null,
  "note": "Remote Ollama instance reachable via tunnel."
}
```

### Step 3: Verify Expected Models (`qwen2.5:14b` & `qwen2.5vl:7b`)
```powershell
curl.exe http://127.0.0.1:8000/v1/ai/models
```
Expected output:
```json
{
  "connected": true,
  "base_url": "http://127.0.0.1:11434",
  "all_expected_present": true,
  "expected_models": ["qwen2.5:14b", "qwen2.5vl:7b"],
  "installed_models": ["qwen2.5:14b", "qwen2.5vl:7b"],
  "verified_models": ["qwen2.5:14b", "qwen2.5vl:7b"],
  "missing_models": [],
  "models": [...],
  "error": null
}
```

### Step 4: Verify in the Explain Studio UI
1. Navigate to `http://127.0.0.1:5173`.
2. Inspect the header badge:
   - When connected: `Ollama: 2/2 Ready` (green dot).
   - If tunnel is closed: `Ollama Offline` (red dot).
   - Click the badge at any time to re-test the connection.

---

## 5. Troubleshooting & FAQ

### Issue: `bind [127.0.0.1]:11434: Address already in use`
- **Cause:** You have a local Ollama process running on your Windows machine, or a previous SSH tunnel is already occupying port 11434.
- **Solution:** 
  - Check what process is using port 11434:
    ```powershell
    Get-NetTCPConnection -LocalPort 11434
    ```
  - Stop the local Windows Ollama service from the system tray or Task Manager, then restart the SSH tunnel.

### Issue: `Connection refused` when curling `127.0.0.1:11434`
- **Cause:** Either the SSH tunnel is not running, or Ollama is stopped on the remote server.
- **Solution:**
  - Verify your SSH tunnel process is still active.
  - SSH into the server and verify Ollama service status: `systemctl status ollama` or `curl http://127.0.0.1:11434/` on the server itself.

### Issue: SSH tunnel drops after some inactivity
- **Cause:** Router or intermediate firewall NAT timeout closing idle TCP connections.
- **Solution:**
  - Ensure `ServerAliveInterval 60` and `ServerAliveCountMax 3` are added to your SSH command or `~/.ssh/config`.

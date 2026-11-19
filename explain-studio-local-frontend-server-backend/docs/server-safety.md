# Server safety rules

1. Do not change NVIDIA drivers or reboot to address the NVML mismatch without the server administrator's approval.
2. Do not change Ollama configuration or unload/restart existing company models.
3. Do not expose Ollama port 11434 publicly.
4. Keep the initial API bound to `127.0.0.1:8000`; use an SSH tunnel for development.
5. Do not install Docker or modify disks as part of this foundation milestone.
6. Deploy under a separate project directory and, later, a dedicated service account.
7. Do not enable GPU workloads until driver status, existing GPU consumers and memory headroom are verified.
8. Keep uploads disabled until file validation, storage quotas and retention are implemented.

# Local Security Notes

This setup is intentionally local-first.

- The bot binds to `127.0.0.1:8080`, not all network interfaces.
- Do not run it with `sudo`.
- Do not open router ports.
- Use the tunnel only while monitoring.
- Stop both the bot and tunnel with `Ctrl+C` when finished.
- Keep `.env` private. It contains Telegram and Helius secrets.
- The `/helius` endpoint requires `WEBHOOK_SECRET`.

Because the Telegram bot token and Helius key were shared in chat during setup,
rotate both after testing if you want the cleanest security posture.

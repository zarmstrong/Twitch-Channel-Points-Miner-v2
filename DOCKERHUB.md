# Twitch Channel Points Miner

Automatically watch configured Twitch channels, earn channel points, claim
Drops and Moments, and participate in predictions. The image supports
`linux/amd64` and `linux/arm64`.

## Quick start

Create a `config` directory and copy
[`config.example.py`](https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3/blob/master/config.example.py)
to `config/config.py`. Customize the configuration before starting the image.

```yaml
services:
  miner:
    image: zacharmstrong/twitch-channel-points-miner:latest
    stdin_open: true
    tty: true
    environment:
      - TERM=xterm-256color
      - TZ=America/Denver
    volumes:
      - ./analytics:/usr/src/app/analytics
      - ./cookies:/usr/src/app/cookies
      - ./logs:/usr/src/app/logs
      - ./config:/usr/src/app/config
    ports:
      - "54455:54455"
```

On first authentication, run the container interactively so you can complete
the Twitch login. The mounted directories preserve configuration, cookies,
logs, and analytics when the container is replaced.

Set `TZ` to an
[IANA time zone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones).
If it is omitted, the container uses UTC.

## Documentation

- [Configuration and complete usage guide](https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3#how-to-use)
- [Docker image build guide](https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3/blob/master/BUILD.md#docker-images)
- [Portainer deployment guide](https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3/wiki/Deploy-with-Portainer)
- [Issue tracker](https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3/issues)

## Disclaimer

This project is not affiliated with or endorsed by Twitch. Use it at your own
risk and in accordance with Twitch's terms and applicable rules.

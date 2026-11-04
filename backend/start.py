"""Container entry point. Hosts often mount a persistent disk at /data owned by root, so the container
starts as root only long enough to hand /data to the unprivileged 'showme' user, then runs the server as
that user. Anything passed on the command line replaces the default web server command."""
import os, pwd, sys

DATA = os.environ.get("APPRENTICE_DATA", "/data")
if os.geteuid() == 0:
    u = pwd.getpwnam("showme")
    os.makedirs(DATA, exist_ok=True)
    for root, dirs, files in os.walk(DATA):
        for name in [root] + [os.path.join(root, n) for n in dirs + files]:
            try:
                os.lchown(name, u.pw_uid, u.pw_gid)
            except OSError:
                pass
    os.setgid(u.pw_gid)
    os.setuid(u.pw_uid)
    os.environ["HOME"] = u.pw_dir
cmd = sys.argv[1:] or ["sh", "-c", "uvicorn apprentice.app:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8000} "
                       "--workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips='*'"]
os.execvp(cmd[0], cmd)

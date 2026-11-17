"""Command line:  python -m a2p <command>

  serve              start the web page (http://localhost:8080)
  migrate            import an old config.json (original app) into settings.json
  add <url>          fetch an article and queue it
  list               show the queue
  run                process the queue (Ctrl+C stops and keeps finished segments)
  startover <id>     forget a stopped job's finished segments
  remove <id>        delete a job
"""
import sys
from . import db, engines, fetch, runner, settings


def main(argv):
    cmd = argv[0] if argv else ''
    if cmd == 'serve':
        from . import web
        web.serve()
    elif cmd == 'migrate':
        from . import migrate
        migrate.run(overwrite='--overwrite' in argv)
    elif cmd == 'add' and len(argv) > 1:
        try:
            print('queued job', fetch.add(url=argv[1]))
        except fetch.NeedsPaste as e:
            print('Could not fetch:', e)
            return 2
    elif cmd == 'list':
        for j in db.jobs():
            segs = db.segments(j['id'])
            done = sum(1 for s in segs if s['path'])
            prog = f' [{done}/{len(segs)} segments]' if segs else ''
            print(f'{j["id"]:>4} {j["status"]:<10} {j["engine_used"] or j["engine"] or "auto":<9} '
                  f'{j["voice_used"] or j["voice"] or "default":<18} {j["title"][:60]}{prog}')
    elif cmd == 'run':
        print('engines:', ', '.join(engines.available()) or 'none', '| default:', engines.default())
        for j in db.jobs(('stopped', 'failed', 'processing')):
            db.update_job(j['id'], status='pending', error=None)
        try:
            runner.run_queue()
        except KeyboardInterrupt:
            runner.stop()
            print('Stopping...')
    elif cmd == 'startover' and len(argv) > 1:
        runner.start_over(int(argv[1]))
    elif cmd == 'remove' and len(argv) > 1:
        db.clear_segments(int(argv[1]))
        db.delete_job(int(argv[1]))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except settings.SettingsError as e:
        print(e)
        sys.exit(1)

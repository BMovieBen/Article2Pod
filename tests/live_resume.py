"""Live check against a running ComfyUI (not a unit test):
run job <id>, press Stop after 3 finished segments, confirm the job is
"stopped" with its segments kept, then run again and confirm only the
missing segments are generated.

    python tests/live_resume.py <job id>
"""
import sys, threading, time
sys.path.insert(0, '.')
from a2p import db, runner

job_id = int(sys.argv[1])
t = threading.Thread(target=runner.run_queue)
t.start()
while t.is_alive():
    time.sleep(2)
    if sum(1 for s in db.segments(job_id) if s['path']) >= 3:
        print('>>> pressing Stop', flush=True)
        runner.stop()
        break
t.join()
job  = db.get_job(job_id)
kept = [s['idx'] for s in db.segments(job_id) if s['path']]
print(f'>>> status after stop: {job["status"]}; kept segments {kept} of {len(db.segments(job_id))}', flush=True)
assert job['status'] == 'stopped' and len(kept) >= 3

db.update_job(job_id, status='pending')
runner.run_queue()
job = db.get_job(job_id)
print(f'>>> final status: {job["status"]}; output: {job["output_path"]}', flush=True)
print('>>> info:', job['info'], flush=True)

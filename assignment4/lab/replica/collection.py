"""Resumable read-only archive transfer with remote identity verification."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

def receive(transport, role, run, receipt):
    run=Path(run);destination=run/(role+'-evidence.tar.gz');partial=run/(role+'-evidence.tar.partial');identity=run/(role+'-transfer.json')
    expected={'sha256':receipt['sha256'],'bytes':receipt['bytes']}
    if destination.exists():
        with destination.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
        if actual==expected['sha256']:
            subprocess.run(['tar','-tzf',str(destination)],check=True,stdout=subprocess.DEVNULL);return destination
    if not identity.exists() or json.loads(identity.read_text())!=expected:
        partial.unlink(missing_ok=True);identity.write_text(json.dumps(expected))
    offset=partial.stat().st_size if partial.exists() else 0
    if offset>expected['bytes']:partial.unlink();offset=0
    if offset<expected['bytes']:
        with partial.open('ab') as output,(run/(role+'-collection.log')).open('ab') as errors:
            result=subprocess.run(transport.args(role,'tail -c +'+str(offset+1)+' /opt/a4-replica/evidence.tar.gz'),stdout=output,stderr=errors)
        if result.returncode:
            transport.refresh_access();raise ConnectionError('Archive transfer interrupted; retained offset '+str(partial.stat().st_size))
    if partial.stat().st_size!=expected['bytes']:raise ConnectionError('Archive length mismatch')
    with partial.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
    if actual!=expected['sha256']:
        partial.rename(partial.with_name(partial.name+'.checksum-failed-'+str(time.time_ns())))
        raise ValueError('Archive checksum mismatch; damaged bytes preserved, retry from zero')
    subprocess.run(['tar','-tzf',str(partial)],check=True,stdout=subprocess.DEVNULL)
    partial.replace(destination);return destination

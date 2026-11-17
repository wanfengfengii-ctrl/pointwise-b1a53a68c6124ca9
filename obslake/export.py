import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from .records import validate_request_id
from .row_codec import canonical

class Exporter:
    def __init__(self, catalog, results, receipts):
        self.catalog, self.results, self.receipts = catalog, results, receipts

    def export_result(self, request_id, result_id, destination):
        validate_request_id(request_id)
        target = Path(destination).absolute()
        identity = canonical([result_id, str(target)])
        replay = self.receipts.lookup('export', request_id, identity)
        if replay is not None:
            return replay
        result = self.results.read_result(result_id)
        if result['kind'] != 'delivery':
            raise ValueError('only delivery results can be exported')
        data = (canonical(result) + '\n').encode('utf-8')
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError('destination must be a regular file')
        temp, backup, replaced = None, None, False
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.delivery-', delete=False) as stream:
                temp = Path(stream.name)
                split = len(data) // 2
                stream.write(data[:split])
                self.catalog.checkpoint('export.mid_write')
                stream.write(data[split:])
                stream.flush()
                os.fsync(stream.fileno())
            if temp.read_bytes() != data:
                raise IOError('export verification failed')
            if target.exists():
                fd, name = tempfile.mkstemp(dir=target.parent, prefix='.previous-')
                os.close(fd)
                backup = Path(name)
                shutil.copyfile(target, backup)
            response = dict(request_id=request_id, result_id=result_id, destination=str(target),
                            digest=hashlib.sha256(data).hexdigest(), byte_count=len(data))
            with self.catalog.transaction():
                self.catalog.checkpoint('export.before_replace')
                os.replace(temp, target)
                temp = None
                replaced = True
                self.catalog.checkpoint('export.after_replace')
                self.receipts.insert('export', request_id, identity, response)
            return response
        except BaseException:
            if replaced:
                if backup is None:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(backup, target)
                    backup = None
            raise
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
            if backup is not None:
                backup.unlink(missing_ok=True)

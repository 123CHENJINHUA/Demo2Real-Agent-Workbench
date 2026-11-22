"""Record real handoffs between participants in an offline run."""

from contextlib import contextmanager
from uuid import uuid4


@contextmanager
def handoff(ctx, sender, recipient, request, *, evidence_path=None):
    data={'exchange_id':uuid4().hex,'sender':sender,'recipient':recipient,
          'request':request,'evidence_path':evidence_path}
    ctx['emit']('agent.handoff_started',data)
    finished=False

    def finish(result, *, summary=None, report_path=None):
        nonlocal finished
        ctx['emit']('agent.handoff_finished',data | {
            'status':result['status'],
            'summary':summary or result.get('reason') or result.get('error'),
            'report_path':report_path or result.get('report_path')})
        finished=True

    try:
        yield finish
    except InterruptedError:
        if not finished:
            finish({'status':'cancelled'},summary='处理已取消')
        raise
    except Exception as exc:
        if not finished:
            finish({'status':'failed'},summary=str(exc))
        raise

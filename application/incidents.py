"""Project knowledge revisions are authoritative; SQLite preserves unarchived failures."""
from datetime import datetime


def merge_incidents(database_records, knowledge_records):
    authoritative={record['incident_id']:record for record in knowledge_records}
    items=[]
    for record in authoritative.values():
        resolution=record.get('resolution',{})
        if not isinstance(resolution,dict):resolution={}
        category=record.get('primary_category','workflow_integrity')
        items.append({
            'id':record['incident_id'], 'incident_id':record['incident_id'],
            'run_id':record.get('run_id'), 'task_id':record.get('task_id'),
            'category':category, 'primary_category':category,
            'status':record.get('status') or record.get('resolution_state') or resolution.get('state') or 'open',
            'summary':record.get('summary') or record.get('observed_behavior') or '未命名问题记录',
            'created_at':record.get('timestamp_utc'), 'record':record,
            'details':record, 'source':'knowledge', 'revision':record.get('revision'),
        })
    for row in database_records:
        details=row.get('details') or {}
        if not isinstance(details,dict):details={}
        nested=details.get('experience_record') or {}
        if not isinstance(nested,dict):nested={}
        links={row.get('id'),details.get('incident_id'),nested.get('incident_id')}
        if any(link in authoritative for link in links if link):continue
        items.append(dict(row)|{'record':details,'source':'sqlite'})
    def timestamp(item):
        value=item.get('created_at')
        if isinstance(value,(float,int)):return float(value)
        try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
        except (ValueError,TypeError,OverflowError):return 0
    return sorted(items,key=lambda item:(timestamp(item),str(item.get('id',''))),reverse=True)

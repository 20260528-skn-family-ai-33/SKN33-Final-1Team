"""Single-process, bounded TTL counters. All decisions hold one short lock."""
from collections import deque
from threading import Lock
import hashlib,hmac,math,time
from .errors import AuthError


class LoginLimits:
    def __init__(self,secret,*,clock=time.monotonic,capacity=10000):
        self.key=secret.reveal().encode();self.clock=clock;self.capacity=capacity;self.lock=Lock();self.entries={}

    def key_for(self,kind,value):
        return hmac.new(self.key,('cc-auth-limit-v1\0'+kind+'\0'+value).encode(),hashlib.sha256).hexdigest()

    def _entry(self,key,now):
        entry=self.entries.get(key)
        if entry is None:
            if len(self.entries)>=self.capacity:raise AuthError('AUTH_RATE_LIMITED',429,retry_after=60)
            entry={'times':deque(),'blocked':0,'expires':now+900};self.entries[key]=entry
        return entry

    def before(self,ip,kind,identifier):
        now=self.clock();ipkey=self.key_for('ip',ip);key=self.key_for(kind,identifier)
        with self.lock:
            for existing in list(self.entries):
                if self.entries[existing]['expires']<=now:del self.entries[existing]
            # Reserve both keys atomically or reject before allocating/performing Argon2.
            missing=sum(k not in self.entries for k in (ipkey,key))
            if len(self.entries)+missing>self.capacity:raise AuthError('AUTH_RATE_LIMITED',429,retry_after=60)
            address=self._entry(ipkey,now);identity=self._entry(key,now)
            while address['times'] and address['times'][0]<=now-60:address['times'].popleft()
            if len(address['times'])>=30:raise AuthError('AUTH_RATE_LIMITED',429,retry_after=max(1,math.ceil(address['times'][0]+60-now)))
            if identity['blocked']>now:raise AuthError('AUTH_RATE_LIMITED',429,retry_after=max(1,math.ceil(identity['blocked']-now)))
            address['times'].append(now);address['expires']=now+60
            identity['expires']=max(now+900,identity['blocked'])
        return key

    def finished(self,key,success):
        now=self.clock()
        with self.lock:
            entry=self.entries.get(key)
            if entry is None:return
            if success:entry['times'].clear();return
            while entry['times'] and entry['times'][0]<=now-900:entry['times'].popleft()
            entry['times'].append(now)
            if len(entry['times'])>=5:entry['blocked']=now+900
            entry['expires']=max(now+900,entry['blocked'])

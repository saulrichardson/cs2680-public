"""Scripted provider fixtures; no model or network calls."""
from mads.store import dump


def call(name, **arguments):
    return {'id':name,'type':'function','function':{'name':name,'arguments':dump(arguments)}}

class Scripted:
    config={'model':'fixture','protocol':'chat_completions'}
    def __init__(self, store, replies, child_replies=None):
        self.store=store; self.replies=iter(replies); self.child_replies=child_replies
        self.requests=[]; self.closed=False
    def request_options(self): return {}
    def complete(self, request, context_id, **kw):
        self.requests.append(request)
        self.last_request_record=self.store.request(request, context_id, **kw)
        calls=next(self.replies)
        if isinstance(calls,Exception): raise calls
        result={'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':'','tool_calls':calls}}]}
        self.last_response_record=self.store.add('response',blob=self.store.blob(dump(result)))
        return result
    def context_rejection(self, exc): return False
    def fork(self,store):
        self.child=Scripted(store,self.child_replies)
        return self.child
    def __enter__(self): return self
    def __exit__(self,*args): self.closed=True

"""Sandbox transport uncertainty is evidence, never permission to replay."""
import http.client
import urllib.error


class SandboxTransportError(RuntimeError):
    def __init__(self, message, job_id=None):
        super().__init__(message)
        self.job_id = job_id

    def observation(self):
        result = {'error': str(self), 'error_kind': 'sandbox_transport',
                  'execution_state': 'unknown', 'executed': None,
                  'replayed': False}
        if self.job_id:
            result['id'] = self.job_id
        return result


def sandbox_call(post, url, payload):
    try:
        return post(url, payload)
    except urllib.error.HTTPError as error:
        with error:
            detail = error.read().decode('utf-8', errors='replace')
        raise SandboxTransportError(f'HTTP {error.code}: {detail}') from error
    except (OSError, http.client.HTTPException, RuntimeError, ValueError) as error:
        # Includes disconnected/invalid HTTP responses. Programming errors and
        # persistence failures outside this transport call remain fatal.
        raise SandboxTransportError(str(error)) from error

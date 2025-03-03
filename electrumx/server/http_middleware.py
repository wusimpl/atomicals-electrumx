# -*- coding: utf-8 -*-

import asyncio
import json
import os
import traceback
from aiohttp import web, web_middlewares

def serialize_data(data):
    if isinstance(data, bytes):
        return base64.b64encode(data).decode('utf-8')
    elif isinstance(data, dict):
        return {key: serialize_data(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [serialize_data(element) for element in data]
    else:
        return data
    
class RateLimiter:
    def __init__(self, max_tokens, refill_interval, delay_after, delay_ms):
        self.tokens = int(max_tokens)
        self.last_refill = asyncio.get_event_loop().time()
        self.max_tokens = int(max_tokens)
        self.refill_interval = float(refill_interval)
        self.delay_after = int(delay_after)
        self.delay_ms = float(delay_ms)
        self.delay_active = False

    async def check_limit(self):
        now = asyncio.get_event_loop().time()
        elapsed = now - self.last_refill
        tokens_to_refill = int(elapsed / self.refill_interval)
        self.tokens = min(self.tokens + tokens_to_refill, self.max_tokens)
        self.last_refill = now
        if self.tokens > 0:
            self.tokens -= 1
            return True
        elif not self.delay_active:
            self.delay_active = True
            await asyncio.sleep(self.delay_after)
            self.delay_active = False
            return True
        return False

rate_limiter = RateLimiter(
    max_tokens=os.environ.get("MAX_TOKENS", 10000),
    refill_interval=os.environ.get("RATE_LIMIT_WINDOW_SECONDS", 3),
    delay_after=os.environ.get("RATE_LIMIT_DELAY_AFTER", 5),
    delay_ms=os.environ.get("RATE_LIMIT_DELAY_MS", 300)
)

def error_resp(status_code: int, exception: Exception) -> web.Response:
    return web.Response(
        status=status_code,
        body=json.dumps({
            "success": False,
            'code': 1,
            'message': str(exception)
        }).encode('utf-8'),
        content_type='application/json')

def success_resp(data) -> web.Response:
    result = {"success": True, "response": serialize_data(data)}
    return web.json_response(data=result)

def request_middleware(self) -> web_middlewares:
    async def factory(app: web.Application, handler):
        async def middleware_handler(request):
            self.logger.info('Request {} comming'.format(request))
            if await request.app['rate_limiter'].check_limit():
                # return await handler(request)
                response = await handler(request)
                if isinstance(response, web.Response):
                    return response
                return success_resp(response)
            await asyncio.sleep(request.app['rate_limiter'].delay_ms / 1000)
            return web.json_response({"success": False, "error": "Rate limit exceeded"}, status=429)
        return middleware_handler
    return factory

def error_middleware(self) -> web_middlewares:
    async def factory(app: web.Application, handler):
        async def middleware_handler(request):
            try:
                response = await handler(request)
                if response.status == 404 or response.status == 400:
                    return error_resp(response.status, Exception(response.text))
                return response
            except web.HTTPException as ex:
                if ex.status == 404:
                    return error_resp(ex.status, ex)
                raise
            except Exception as e:
                self.logger.warning('Request {} has failed with exception: {}'.format(request, repr(e)))
                self.logger.warning(traceback.format_exc())
                return error_resp(500, e)
        return middleware_handler
    return factory
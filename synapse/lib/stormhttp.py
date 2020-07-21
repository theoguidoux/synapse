import json

import aiohttp

import synapse.exc as s_exc

import synapse.lib.stormtypes as s_stormtypes

@s_stormtypes.registry.registerLib
class LibHttp(s_stormtypes.Lib):
    '''
    A Storm Library exposing an HTTP client API.
    '''

    _storm_lib_path = ('inet', 'http')

    def getObjLocals(self):
        return {
            'get': self._httpEasyGet,
            'post': self._httpPost,
        }

    async def _httpEasyGet(self, url, headers=None, ssl_verify=True):
        '''
        Get the contents of a given URL.

        Args:
            url (str): The URL to retrieve.

            headers (dict): HTTP headers to send with the request.

            ssl_verify (bool): Perform SSL/TLS verification. Defaults to true.

        Returns:
            HttpResp: A Storm HttpResp object.
        '''
        url = await s_stormtypes.toprim(url)
        headers = await s_stormtypes.toprim(headers)

        kwargs = {}
        if not ssl_verify:
            kwargs['ssl'] = False
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, headers=headers, **kwargs) as resp:
                info = {
                    'code': resp.status,
                    'body': await resp.content.read(),
                }
                return HttpResp(info)

    async def _httpPost(self, url, headers=None, json=None, body=None, ssl_verify=True):
        '''
        Post data to a given URL.

        Args:
            url (str): The URL to post to.

            headers (dict): HTTP headers to send with the request.

            json: The data to post, as JSON object.

            body: The data to post, as binary object.

            ssl_verify (bool): Perform SSL/TLS verification. Defaults to true.

        Returns:
            HttpResp: A Storm HttpResp object.
        '''

        url = await s_stormtypes.toprim(url)
        json = await s_stormtypes.toprim(json)
        body = await s_stormtypes.toprim(body)
        headers = await s_stormtypes.toprim(headers)

        kwargs = {}
        if not ssl_verify:
            kwargs['ssl'] = False

        async with aiohttp.ClientSession() as sess:
            try:
                async with sess.post(url, headers=headers, json=json, data=body, **kwargs) as resp:
                    info = {
                        'code': resp.status,
                        'body': await resp.content.read()
                    }
                    return HttpResp(info)
            except ValueError as e:
                mesg = f'Error during http post - {str(e)}'
                raise s_exc.StormRuntimeError(mesg=mesg, headers=headers, json=json, body=body) from None

@s_stormtypes.registry.registerType
class HttpResp(s_stormtypes.StormType):

    def __init__(self, locls):
        s_stormtypes.StormType.__init__(self)
        self.locls.update(locls)
        self.locls.update(self.getObjLocals())

    def getObjLocals(self):
        return {
            'json': self._httpRespJson,
        }

    async def _httpRespJson(self):
        body = self.locls.get('body')
        return json.loads(body)

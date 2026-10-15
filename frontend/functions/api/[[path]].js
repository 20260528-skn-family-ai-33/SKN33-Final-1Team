// Pages에서만 중계 키를 붙이고 브라우저에는 같은 출처의 API를 제공한다.
export async function onRequest({ request, env }) {
  let upstream;
  try {
    upstream = new URL(env.API_ORIGIN);
    if (upstream.protocol !== 'https:' || upstream.username || upstream.password
        || upstream.origin === new URL(request.url).origin || !env.API_PROXY_SECRET) throw new Error();
  } catch {
    return Response.json({ detail: '서버 연결이 준비되지 않았습니다.' }, { status: 503 });
  }

  const incoming = new URL(request.url);
  upstream.pathname = incoming.pathname;
  upstream.search = incoming.search;
  const headers = new Headers(request.headers);
  headers.delete('Host');
  headers.set('X-Review-Proxy-Key', env.API_PROXY_SECRET);
  try {
    const response = await fetch(upstream, {
      method: request.method,
      headers,
      body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
      redirect: 'manual',
    });
    const result = new Response(response.body, response);
    result.headers.set('Cache-Control', 'no-store');
    return result;
  } catch {
    return Response.json({ detail: '검수 서버에 연결하지 못했습니다.' }, { status: 502 });
  }
}

function handler(event) {
  var request = event.request;
  var path = request.uri.replace(/\/+$/, "");
  var pages = ["/login", "/review/new", "/review/tasks", "/video", "/video/logo", "/modeltest"];

  if ((request.method === "GET" || request.method === "HEAD") &&
      pages.indexOf(path) !== -1) {
    request.uri = "/index.html";
  }

  return request;
}
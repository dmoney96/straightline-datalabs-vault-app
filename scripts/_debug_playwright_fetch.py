from headless_fetch import headless_fetch_html
url = "https://httpbingo.org/status/403"
final_url, html = headless_fetch_html(url, timeout_ms=20000)
print("final_url:", final_url)
print("html_len:", len(html))
print(html[:200].replace("\n"," ") )

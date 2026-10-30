#!/usr/bin/env python3
"""Render the reviewed planning content. --check never changes files."""
from pathlib import Path
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote, quote
import argparse, hashlib, json, re, sys
from single_planning import SINGLE_NAME, render_single

PLAN = Path(__file__).resolve().parents[1]
ROOT = PLAN.parent.parent
CONTENT = PLAN / 'content'
OUT = PLAN / 'organized'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

class Inspect(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids=[]; self.links=[]; self.tabs=[]; self.panels=[]; self.resources=[]
    def handle_starttag(self,t,a):
        a=dict(a)
        if 'id' in a:self.ids.append(a['id'])
        if t=='a' and 'href' in a:self.links.append(a['href'])
        if a.get('role')=='tab':self.tabs.append(a)
        if a.get('role')=='tabpanel':self.panels.append(a)
        if t in ('script','img','link') and a.get('src',a.get('href','')).startswith(('http:','https:')):self.resources.append(a)

EXTRA_CSS = '''
.doc-panel{display:block}.doc-panel[hidden]{display:none!important}
.hero{padding-bottom:28px}.hero h1{font-size:clamp(30px,4.3vw,55px)}.hero p{max-width:940px}
.doc-nav{gap:6px;flex-wrap:wrap}.doc-nav a{font-size:11px;padding:8px 12px}
.release-strip{margin:16px 0 0;padding:14px 16px;background:#f0f3e8;border:1px solid var(--line);border-radius:12px;font-size:12px;line-height:1.8}
.release-strip b{color:var(--ink)}.doc-meta{font-size:12px;color:var(--muted);margin:12px 0;line-height:1.8}
.doc-panel p,.doc-panel li{line-height:1.85}.doc-panel h3{margin-top:30px}.doc-panel h4{margin-top:24px}
.table-wrap{overflow:auto}table{min-width:580px}th{white-space:normal}td,th{overflow-wrap:anywhere}td:first-child{min-width:80px}
.catalog{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.catalog a{display:block;padding:18px;border:1px solid var(--line);border-radius:12px;background:#f6f8ef;text-decoration:none}.catalog b{display:block;margin-bottom:5px}.catalog small{display:block;color:var(--muted)}
.review-note{border-left:4px solid #aa813f;background:#faf3e3;padding:15px 18px;margin:22px 0;border-radius:0 10px 10px 0}.review-note p{margin:7px 0}
.flow{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;counter-reset:steps}.flow li{font-size:13px;padding:15px;counter-increment:steps}.flow li::before{content:counter(steps,decimal-leading-zero);display:block;font-weight:800;color:#647c59;margin-bottom:6px}.flow li:not(:last-child)::after{display:none}.flow span{font-size:12px}
.workflow-flow li:nth-child(3),.workflow-flow li:nth-child(7),.workflow-flow li:nth-child(9){background:#faf0d9;border-color:#cfb775}
.status-chip{display:inline-block;padding:3px 8px;border:1px solid var(--line);border-radius:20px;font-size:11px;background:#f2f5ed}.wireframe{padding:18px;border:1px dashed #9caf9c;border-radius:12px;background:#f7f9f3}.wireframe-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.wireframe-box{padding:15px;border:1px solid var(--line);background:#fffefb;border-radius:9px}
.findbox{margin:0 0 18px;display:flex;gap:8px;flex-wrap:wrap}.findbox input{flex:1;min-width:160px;border:1px solid var(--line);border-radius:9px;padding:10px 12px;background:#fffefb;color:var(--ink);font:inherit;font-size:13px}.find-results{display:grid;gap:6px;margin-bottom:16px}.find-results button{text-align:left;font-size:12px;padding:10px}.find-results:empty{display:none}.find-count{font-size:12px;color:var(--muted)}
.doc-panel pre{white-space:pre-wrap;overflow-wrap:anywhere}.doc-panel code{overflow-wrap:anywhere}.doc-panel details{border:1px solid var(--line);border-radius:12px;margin:14px 0;padding:14px}.doc-panel summary{font-weight:700;cursor:pointer}.evidence-grid{font-size:12px}.muted{color:var(--muted)}
@media(max-width:800px){.catalog,.wireframe-grid{grid-template-columns:1fr}.flow{grid-template-columns:1fr}.doc-nav a{font-size:10px}.hero{padding:28px 20px}.doc-panel{padding:22px}.release-strip{font-size:11px}}
@media print{.doc-panel[hidden]{display:block!important}.findbox,.find-results,.find-count,.panel-footer{display:none!important}.doc-panel{break-before:page}.catalog{grid-template-columns:1fr 1fr}table{min-width:0}.flow{grid-template-columns:repeat(3,1fr)}}
'''

JS = r'''
(()=>{'use strict';const tabs=[...document.querySelectorAll('[role=tab]')],panels=tabs.map(t=>document.getElementById(t.dataset.panel));let active=0;
function activate(i,focus=false,updateHash=true){active=(i+tabs.length)%tabs.length;tabs.forEach((t,j)=>{t.setAttribute('aria-selected',String(j===active));t.tabIndex=j===active?0:-1;panels[j].hidden=j!==active});if(updateHash)history.replaceState(null,'','#'+panels[active].id);if(focus)tabs[active].focus();}
tabs.forEach((t,i)=>{t.onclick=()=>activate(i);t.onkeydown=e=>{const keys={ArrowDown:i+1,ArrowRight:i+1,ArrowUp:i-1,ArrowLeft:i-1,Home:0,End:tabs.length-1};if(Object.hasOwn(keys,e.key)){e.preventDefault();activate(keys[e.key],true)}}});
document.querySelectorAll('[data-goto]').forEach(b=>b.onclick=()=>{const i=panels.findIndex(p=>p.id===b.dataset.goto);if(i>=0)activate(i)});
document.querySelectorAll('[data-next]').forEach(b=>b.onclick=()=>activate(active+1));document.querySelectorAll('[data-prev]').forEach(b=>b.onclick=()=>activate(active-1));
function route(){let id;try{id=decodeURIComponent(location.hash.slice(1))}catch{return}const e=document.getElementById(id);const p=e?.closest('[role=tabpanel]');const i=panels.indexOf(p);if(i>=0){activate(i,false,false);let parent=e;while(parent&&parent!==p){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement}if(e!==p)e.scrollIntoView({block:'center'})}}activate(0,false,false);route();window.addEventListener('hashchange',route);
const q=document.getElementById('doc-search'),res=document.getElementById('find-results'),count=document.getElementById('find-count');
q.oninput=()=>{res.replaceChildren();const s=q.value.trim().toLocaleLowerCase();if(!s){count.textContent='';return}let n=0;panels.forEach((p,i)=>{const text=p.textContent.replace(/\s+/g,' '),pos=text.toLocaleLowerCase().indexOf(s);if(pos<0)return;n++;const b=document.createElement('button');b.type='button';b.textContent=tabs[i].textContent.trim()+' · '+text.slice(Math.max(0,pos-30),pos+110);b.onclick=()=>{activate(i);p.scrollIntoView({block:'start'})};res.append(b)});count.textContent=n+'개 절에서 찾았습니다.'};
document.getElementById('clear-search').onclick=()=>{q.value='';q.oninput();q.focus()};
let saved;window.addEventListener('beforeprint',()=>{saved={active,details:[...document.querySelectorAll('details')].map(d=>[d,d.open])};panels.forEach(p=>p.hidden=false);saved.details.forEach(([d])=>d.open=true)});window.addEventListener('afterprint',()=>{if(saved){activate(saved.active);saved.details.forEach(([d,o])=>d.open=o)}});
})();
'''

def table(headers,rows):
    return '<div class="table-wrap"><table><thead><tr>'+''.join('<th scope="col">'+escape(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(c)+'</td>' for c in row)+'</tr>' for row in rows)+'</tbody></table></div>'

def checks(base,reference=None):
    errors=[]
    for path,key in [(PLAN/base['master_snapshot'],'master_sha256'),(PLAN/base['archive'],'archive_sha256')]:
        if not path.exists() or sha(path)!=base[key]:errors.append('기준/보관 파일 변경: '+str(path))
    original=reference or Path.home()/base['source_original_rel_to_home']
    if reference and not original.exists():errors.append('지정한 원본 통합문서가 없습니다: '+str(original))
    if original.exists() and sha(original)!=base['master_sha256']:errors.append('원본 통합문서가 바뀌었습니다. 내용 대조 및 baseline 개정이 필요합니다.')
    for p,h in base['code_evidence'].items():
        if not (ROOT/p).exists() or sha(ROOT/p)!=h:errors.append('코드 근거 재검토 필요: '+p)
    return errors

def source_panel(base,doc):
    return {'id':'updates','title':'버전·출처·업데이트','html':f'''
<p class="lead">문서 개정일, 코드 확인일, 실험 실행일을 각각 기록합니다.</p>
<p>문서 묶음 <strong>{escape(base['release'])}</strong> · 검토 기준일 {base['reviewed_date']} KST. 현재 구현에는 10/30 PG 웹 저장·조회 2건 실측과 최신 읽기 전용 집계를 반영합니다. 10/21 그래프 실험은 과거 기록이며 Neo4j 서비스·운영 배포는 후속입니다.</p>
<h3>적용 우선순위</h3><ol><li>2026-10-30 사용자 결정: PostgreSQL + Neo4j를 현재 목표 구조로 반영. 팀 승인 여부는 별도.</li><li>10/30 통합 초안의 범위·정책·미정 항목.</li><li>관련 소스의 현재 구현, 날짜·환경·표본이 명시된 실험 기록.</li><li>이전 planning 문서는 당시 기록으로 보존.</li></ol>
<p>사용자 결정은 목표를 바꾸며, 과거 실험 사실이나 구현 완료 여부를 바꾸지 않습니다. Qdrant·BGE-M3 검색 실험은 역사 자료이며 현재 필수 흐름에 포함하지 않습니다.</p>
{table(['기준 자료','식별·확인 범위'],[['통합 초안',f'<a href="../{base["master_snapshot"]}">보존한 기준 문서 열기</a><br><code>{base["master_sha256"]}</code>'],['추가 사용자 결정','U-DB-20261030 · PostgreSQL 원천 + Neo4j 관계 조회 · 팀 승인 별도'],['코드 확인',f'HEAD {base["code_head"]} + 로컬 변경 포함. 기존 웹 PG 저장·조회 연결, 별도 v1 API 유지'],['이전 문서 보관',f'<a href="../{base["archive"]}">기존 HTML 11개 원본 ZIP</a> · 변경 전 파일 해시 포함'],['이번 문서의 역할',escape(doc['title'])+' · 통합 초안의 내용을 목적별로 재구성']])}
<h3>다음 업데이트 방법</h3><ol><li>통합 초안·사용자 결정·구현/실험 근거의 변경을 대조합니다.</li><li><code>content/planning-content.json</code>의 해당 문서와 공통 상태를 수정합니다. 수치는 표본·날짜·단위·원자료를 함께 갱신합니다.</li><li><code>content/baseline.json</code>의 검토일·개정 번호·근거 해시를 실제 재검토 후 갱신합니다.</li><li>생성 후 <code>python3 docs/planning/tools/build_planning.py --check</code>로 링크·버전·해시·문서 일치 여부를 확인합니다.</li></ol>
<p>기준 파일이나 코드가 바뀌면 검사에서 갱신 필요를 알립니다. 날짜만 자동으로 새로 찍거나, 오래된 실험을 새 측정으로 바꾸지 않습니다. 외부 Google 문서·요구사항/WBS는 이번 로컬 개정의 자동 동기화 대상이 아닙니다.</p>
<h3>PostgreSQL 웹 연결 추가 반영</h3><p>신규 웹 검수 2건·기준/결과 각 14개·OCR 토큰 36개·항목 근거 14개를 검증했습니다. 기존 파일 472개와 DB 행은 보존했습니다. 새로고침·서버 재시작·JSON 사본 없는 DB 조회를 확인했으며, 현재 BackgroundTasks와 별도 durable worker를 구분합니다. Neo4j·outbox·MCP·관계 화면은 향후 연결입니다.</p><h3>기존 공통 변경 이력</h3><ul><li>포스터와 숏폼을 필수 목표로 통일하고, 기준·수정 요청·최종 승인의 세 지점을 연결했습니다.</li><li>현재 코드·부분 연결·독립 실험·추가 구현·팀 미정 항목을 나누었습니다.</li><li>모델 후보와 과거 학습 산출물, OCR·검색·분류·조건 추출 지표를 구분했습니다.</li><li>문서 전반의 PostgreSQL + Neo4j 목표와 ID·버전·권한·재검수 의미를 맞췄습니다.</li></ul>
'''}

def render(name,doc,all_docs,base,css):
    panels=doc['panels']+[source_panel(base,doc)]
    nav=''.join(f'<a href="./{quote(n)}"'+(' aria-current="page"' if n==name else '')+f'>{escape(n[:2])} {escape(d.get("nav",d["title"]))}</a>' for n,d in all_docs.items())
    tabs=''.join(f'<button type="button" role="tab" id="tab-{p["id"]}" aria-controls="{p["id"]}" data-panel="{p["id"]}" aria-selected="{str(i==0).lower()}" tabindex="{0 if i==0 else -1}"><span>{i+1:02}</span>{escape(p["title"])}</button>' for i,p in enumerate(panels))
    body=''.join(f'<section class="doc-panel" id="{p["id"]}" role="tabpanel" aria-labelledby="tab-{p["id"]}" tabindex="0"><h2>{escape(p["title"])}</h2>{p["html"]}<div class="panel-footer"><button type="button" data-prev>← 이전</button><span>{i+1} / {len(panels)}</span><button type="button" data-next>다음 →</button></div></section>' for i,p in enumerate(panels))
    metadata=json.dumps({'release':base['release'],'reviewed_date':base['reviewed_date'],'master_sha256':base['master_sha256'],'document':name,'status':'사용자 목표 반영 · 팀 승인 별도'},ensure_ascii=False).replace('</','<\\/')
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="planning-release" content="{base['release']}"><title>크리에이티브체커 · {escape(doc['title'])}</title><style>{css}\n{EXTRA_CSS}</style></head><body><a class="skip" href="#main">본문으로 바로가기</a>
<header class="site-head"><div class="brand-row"><a class="brand" href="./01_기획서.html"><small>SKN33 FINAL · 1TEAM</small><span class="brand-mark">C</span>크리에이티브체커</a><span class="badge warm">통합 개정 · {base['release']}</span></div><nav class="doc-nav" aria-label="Planning 문서 전체">{nav}</nav></header>
<div class="hero"><span class="eyebrow">PROJECT PLANNING / {name[:2]}</span><h1>{escape(doc['title'])}</h1><p>{escape(doc['subtitle'])}</p><span class="badge">PostgreSQL 원천 + Neo4j 관계 조회</span><span class="badge">포스터 · 숏폼</span><span class="badge warm">팀 승인 별도</span><div class="release-strip"><b>기준일 {base['reviewed_date']} KST</b> · 10/30 통합 초안 + 최신 사용자 DB 결정 반영<br>현재 코드 · 독립 실험 · 추가 구현을 구분합니다. 실험의 실행 날짜와 문서 개정일은 다릅니다.</div></div>
<div class="layout"><aside class="side"><p class="caption">CONTENTS / {len(panels):02}</p><div class="tablist" role="tablist" aria-label="문서 목차" aria-orientation="vertical">{tabs}</div><p class="side-note">이 폴더의 현재 기준 문서 묶음<br>{base['release']}<br>변경 이력은 마지막 탭에서 확인합니다.</p></aside><main id="main"><div class="findbox"><input id="doc-search" type="search" aria-label="이 문서 전체 검색" placeholder="이 문서 전체 검색"><button type="button" id="clear-search">검색 초기화</button></div><p id="find-count" class="find-count" role="status" aria-live="polite"></p><div id="find-results" class="find-results"></div>{body}</main></div>
<footer><p>Creative Checker · {escape(doc['title'])} · {base['release']}</p><p>기존 파일명은 링크 호환을 위해 유지합니다. 본문의 개정 번호가 현재 버전입니다.</p></footer><noscript><p>JavaScript 없이 모든 절을 순서대로 읽을 수 있습니다.</p></noscript><script type="application/json" id="planning-metadata">{metadata}</script><script>document.body.dataset.initialHash=location.hash;</script><script>{JS}</script></body></html>'''

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--check',action='store_true');ap.add_argument('--reference',type=Path);args=ap.parse_args()
    base=json.loads((CONTENT/'baseline.json').read_text());docs=json.loads((CONTENT/'planning-content.json').read_text());css=(CONTENT/'planning.css').read_text()
    errors=checks(base,args.reference);rendered={n:render(n,d,docs,base,css) for n,d in docs.items()}
    updates=source_panel(base,{'title':'통합 Planning'})['html']
    single=render_single(docs,base,updates,PLAN)
    single_inspect=Inspect();single_inspect.feed(single)
    if len(set(single_inspect.ids))!=len(single_inspect.ids):errors.append(SINGLE_NAME+': 중복 ID')
    if single_inspect.resources:errors.append(SINGLE_NAME+': 외부 런타임 리소스')
    for link in single_inspect.links:
        u=urlsplit(link)
        if u.scheme or u.netloc:continue
        if not u.path and u.fragment and unquote(u.fragment) not in single_inspect.ids:errors.append(SINGLE_NAME+': 앵커 없음 '+link)
        if u.path and not (PLAN/unquote(u.path)).exists():errors.append(SINGLE_NAME+': 참고 소스 링크 없음 '+link)
    if args.check and (not (PLAN/SINGLE_NAME).exists() or (PLAN/SINGLE_NAME).read_text()!=single):errors.append(SINGLE_NAME+': 생성본과 검토 내용 불일치')
    for name,s in rendered.items():
        p=Inspect();p.feed(s)
        if len(set(p.ids))!=len(p.ids):errors.append(name+': 중복 ID')
        if p.resources:errors.append(name+': 외부 런타임 리소스')
        for link in p.links:
            u=urlsplit(link)
            if u.scheme or u.netloc:continue
            if not u.path:
                if u.fragment and u.fragment not in p.ids:errors.append(name+': 앵커 없음 '+link)
            else:
                target=(OUT/unquote(u.path)).resolve()
                if target.name not in rendered or target.parent!=OUT:
                    if not target.exists():errors.append(name+': 로컬 링크 없음 '+link)
                if u.fragment and target.parent==OUT and target.name in rendered:
                    other=Inspect();other.feed(rendered[target.name])
                    if u.fragment not in other.ids:errors.append(name+': 연결 문서 앵커 없음 '+link)
        if args.check and (not (OUT/name).exists() or (OUT/name).read_text()!=s):errors.append(name+': 생성본과 검토 내용 불일치')
    if errors:
        print(json.dumps({'passed':False,'errors':errors},ensure_ascii=False,indent=2));return 1
    if not args.check:
        previous_path=CONTENT/'release-manifest.json'
        old_manifest=json.loads(previous_path.read_text()) if previous_path.exists() else {}
        previous=old_manifest['files'] if old_manifest else json.loads((CONTENT/'original-hashes.json').read_text())
        for name in rendered:
            if (OUT/name).exists() and name in previous and sha(OUT/name)!=previous[name]:raise SystemExit('외부 수정 감지, 덮어쓰기 중단: '+name)
        single_path=PLAN/SINGLE_NAME
        if single_path.exists() and sha(single_path)!=old_manifest.get('single_file',{}).get('sha256'):
            raise SystemExit('외부 수정 감지, 덮어쓰기 중단: '+SINGLE_NAME)
        for name,s in rendered.items():
            if not (OUT/name).exists() or (OUT/name).read_text()!=s:
                temp=OUT/(name+'.tmp');temp.write_text(s);temp.replace(OUT/name)
        if not single_path.exists() or single_path.read_text()!=single:
            temp=PLAN/(SINGLE_NAME+'.tmp');temp.write_text(single);temp.replace(single_path)
        manifest={'release':base['release'],'files':{name:sha(OUT/name) for name in rendered},'source_sha256':base['master_sha256'],'documents':len(rendered),'single_file':{'path':SINGLE_NAME,'sha256':sha(single_path)}}
        previous_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps({'passed':True,'mode':'check' if args.check else 'build','documents':len(rendered),'single_file':SINGLE_NAME,'release':base['release'],'code_evidence_files':len(base['code_evidence']),'new_experiments':False},ensure_ascii=False))
    return 0

if __name__=='__main__':sys.exit(main())

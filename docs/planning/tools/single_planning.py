"""Compose all reviewed planning chapters into one offline HTML file."""
import base64
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import unicodedata
from urllib.parse import unquote, urlsplit, quote

SINGLE_NAME = 'Creative_Checker_Planning.html'


class Fragment(HTMLParser):
    def __init__(self, number, names, plan):
        super().__init__(convert_charrefs=False)
        self.prefix = 'd'+number+'-'
        self.names = names
        self.plan = plan
        self.parts = []

    def rewrite(self, tag, attrs):
        result=[]
        for key,value in attrs:
            if value is None:
                result.append((key,value));continue
            if key=='id':value=self.prefix+value
            elif key in ('aria-labelledby','aria-describedby','aria-controls','headers'):
                value=' '.join(self.prefix+x for x in value.split())
            elif key=='href':
                u=urlsplit(value)
                name=unicodedata.normalize('NFC',Path(unquote(u.path)).name)
                if not u.scheme and not u.netloc:
                    if name in self.names:
                        value='#'+self.names[name]+('-'+unquote(u.fragment) if u.fragment else '')
                    elif not u.path and u.fragment:
                        value='#'+self.prefix+unquote(u.fragment)
                    elif u.path:
                        target=(self.plan/'organized'/unquote(u.path)).resolve()
                        # Small source documents and archived originals travel with the single file.
                        if target.is_file() and target.is_relative_to(self.plan/'sources'):
                            mime='text/html;charset=utf-8' if target.suffix=='.html' else 'application/json'
                            value='data:'+mime+';base64,'+base64.b64encode(target.read_bytes()).decode()
                            if not any(k=='download' for k,v in attrs):result.append(('download',target.name))
                        elif target.is_file() and target.suffix=='.zip' and target.parent==self.plan/'archive':
                            value='data:application/zip;base64,'+base64.b64encode(target.read_bytes()).decode()
                            result.append(('download',target.name))
                        else:
                            value=quote(os.path.relpath(target,self.plan),safe='/')+('#'+u.fragment if u.fragment else '')
            # SVG identifiers, including arrow marker references, need the same namespace.
            if key not in ('href','src'):
                import re
                value=re.sub(r'url\(#([^\)]+)\)',lambda m:'url(#'+self.prefix+m[1]+')',value)
            result.append((key,value))
        return result

    def start(self,tag,attrs,end=False):
        attrs=self.rewrite(tag,attrs)
        if tag in ('h2','h3','h4','h5'):tag='h'+str(min(6,int(tag[1])+1))
        self.parts.append('<'+tag+''.join(' '+k+('="'+escape(v,quote=True)+'"' if v is not None else '') for k,v in attrs)+(' />' if end else '>'))

    def handle_starttag(self,tag,attrs):self.start(tag,attrs)
    def handle_startendtag(self,tag,attrs):self.start(tag,attrs,True)
    def handle_endtag(self,tag):
        if tag in ('h2','h3','h4','h5'):tag='h'+str(min(6,int(tag[1])+1))
        self.parts.append('</'+tag+'>')
    def handle_data(self,data):self.parts.append(data)
    def handle_entityref(self,name):self.parts.append('&'+name+';')
    def handle_charref(self,name):self.parts.append('&#'+name+';')
    def handle_comment(self,data):self.parts.append('<!--'+data+'-->')


CSS = '''
:root{--paper:#f4f3ee;--card:#fffefb;--ink:#203d34;--muted:#6d796c;--accent:#315b48;--line:#dbded3}
[hidden]{display:none!important}*{box-sizing:border-box}html{scroll-behavior:auto;scroll-padding-top:95px}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.85 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;word-break:keep-all;overflow-wrap:anywhere}a{color:var(--accent);text-underline-offset:3px}button,input{font:inherit}button{cursor:pointer;color:var(--ink);border:1px solid var(--line);background:var(--card);padding:8px 12px;border-radius:9px}button:hover{background:#eaf0e2}a:focus-visible,button:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid #b78942;outline-offset:3px}.skip{position:absolute;left:10px;top:-100px}.skip:focus{top:8px;z-index:50;background:white;padding:10px}
.single-bar{position:sticky;top:0;z-index:20;background:#f4f3eef5;backdrop-filter:blur(12px);border-bottom:1px solid var(--line);padding:13px max(24px,calc((100vw - 1420px)/2));display:flex;align-items:center;gap:15px}.single-brand{text-decoration:none;font-weight:800;white-space:nowrap}.single-brand small{font-size:10px;letter-spacing:1.5px;display:block;color:var(--muted)}.single-brand span{font-size:19px}.top-tools{margin-left:auto;display:flex;gap:7px;align-items:center}.version-pill{font-size:11px;white-space:nowrap;padding:5px 9px;background:#e6ecde;border-radius:20px}.top-tools button{font-size:12px;white-space:nowrap}.hero-single{max-width:1420px;margin:auto;padding:34px 28px 28px}.hero-single .eyebrow{font-size:11px;font-weight:800;letter-spacing:2px;color:var(--muted)}.hero-single h1{font-size:clamp(32px,4.5vw,53px);line-height:1.25;letter-spacing:-2px;margin:10px 0 14px}.hero-single>p{margin:0 0 14px;color:var(--muted)}.single-badges{display:flex;gap:6px;flex-wrap:wrap}.single-badges span{font-size:11px;border:1px solid var(--line);border-radius:20px;padding:4px 9px;background:#e8eddf}.single-badges span:last-child{background:#f5eadb}.file-note{font-size:12px;margin-top:12px;color:var(--muted)}
.single-layout{max-width:1420px;margin:auto;padding:0 28px 55px;display:grid;grid-template-columns:235px minmax(0,1fr);gap:24px;align-items:start}.single-nav{position:sticky;top:94px;max-height:calc(100vh - 112px);overflow:auto;padding:6px 3px 12px}.single-nav .caption{font-size:10px;letter-spacing:2px;margin:0 10px 12px;color:var(--muted)}.chapter-entry{margin-bottom:5px}.chapter-link{display:flex;gap:10px;padding:9px 11px;text-decoration:none;font-size:12px;align-items:center;border-radius:9px}.chapter-link .number{font-size:10px;opacity:.65;min-width:17px}.chapter-entry.active>.chapter-link{background:var(--ink);color:white}.chapter-sections{margin:4px 0 8px 11px;border-left:1px solid var(--line);padding-left:10px}.chapter-sections a{display:block;font-size:11px;text-decoration:none;padding:5px 8px;color:var(--muted)}.chapter-sections a:hover{background:#e6ecdf;color:var(--ink);border-radius:6px}
.single-main{min-width:0}.search-shell{background:var(--card);border:1px solid var(--line);padding:12px;border-radius:14px;margin-bottom:16px}.search-line{display:flex;gap:7px}.search-line input{min-width:0;flex:1;padding:9px 11px;color:var(--ink);border:1px solid var(--line);border-radius:8px;background:#fff}.search-line button{font-size:12px}.search-count{font-size:12px;color:var(--muted);margin:8px 2px 0}.search-list{display:grid;gap:7px;max-height:360px;overflow:auto;margin-top:9px}.search-list:empty{display:none}.search-list button{text-align:left;padding:10px 12px;line-height:1.65}.search-list b,.search-list small{display:block}.search-list b{font-size:12px}.search-list small{font-size:11px;color:var(--muted);margin-top:4px}.search-list mark{background:#f5df9f;color:var(--ink)}.search-focus{outline:2px solid #ceb169;outline-offset:5px;border-radius:3px}
.single-chapter{min-width:0}.chapter-heading{padding:23px 28px;background:#e8eddf;border:1px solid var(--line);border-radius:16px;margin-bottom:16px}.chapter-heading .eyebrow{font-size:10px;letter-spacing:1.7px;color:var(--muted)}.chapter-heading h2{margin:7px 0 9px;font-size:29px;letter-spacing:-1px;line-height:1.4}.chapter-heading p{margin:0;font-size:12px;color:var(--muted)}.single-section{padding:27px 30px;border:1px solid var(--line);background:var(--card);border-radius:16px;margin:0 0 16px}.single-section>h3{font-size:22px;letter-spacing:-.6px;line-height:1.45;margin:0 0 22px}.single-section h4{font-size:16px;margin:28px 0 13px}.single-section h5{font-size:14px;margin:22px 0 10px}.single-section p{margin:12px 0}.single-section li{margin:5px 0}.single-section .lead{font-size:17px;line-height:1.8}.single-section details{border:1px solid var(--line);border-radius:11px;padding:14px 15px;margin:13px 0}.single-section summary{cursor:pointer;font-weight:700}.single-section pre{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;padding:16px;background:#eef2e8;border-radius:10px;font-size:12px}.single-section code{font-size:.9em;overflow-wrap:anywhere}.single-section .muted{color:var(--muted)}.table-wrap{overflow:auto;margin:19px 0;border:1px solid var(--line);border-radius:10px}table{width:100%;border-collapse:collapse;min-width:560px;font-size:12px;line-height:1.8}th,td{text-align:left;padding:11px 12px;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}th{background:#e9eee1;color:var(--ink);font-weight:800}tr:nth-child(even) td{background:#f8f9f2}caption{text-align:left;padding:10px 12px;font-size:11px;color:var(--muted);background:#f5f7ef}tbody tr:last-child td{border-bottom:0}.review-note,.note{padding:14px 17px;border-left:4px solid #b9985a;border-radius:0 10px 10px 0;background:#f7efdf;margin:19px 0}.grid,.wireframe-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.grid.three{grid-template-columns:repeat(3,minmax(0,1fr))}.card,.wireframe-box{border:1px solid var(--line);background:#f6f8ef;border-radius:11px;padding:17px}.card h4{margin:4px 0 10px}.card p{font-size:12px}.eyebrow,.phase-label{font-size:10px;color:var(--muted);letter-spacing:1px}.catalog{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:16px}.catalog a{display:block;text-decoration:none;padding:13px;background:#eef2e5;border:1px solid var(--line);border-radius:9px}.catalog b,.catalog small{display:block}.catalog small{font-size:11px;color:var(--muted);margin-top:5px}.flow{list-style:none;padding:0;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:11px;counter-reset:steps}.flow li{border:1px solid var(--line);padding:13px;background:#edf2e6;border-radius:10px;counter-increment:steps;font-size:12px}.flow li::before{content:'STEP ' counter(steps,decimal-leading-zero);display:block;font-size:9px;letter-spacing:1px;color:var(--muted);margin-bottom:7px}.flow b,.flow span{display:block}.flow span{font-size:11px;color:var(--muted);margin-top:5px}.workflow-flow li:nth-child(3),.workflow-flow li:nth-child(7),.workflow-flow li:nth-child(9){background:#faf0d9;border-color:#cfb775}.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.metrics>div{background:#edf2e6;padding:16px;border:1px solid var(--line);border-radius:11px}.metrics span,.metrics small{display:block;color:var(--muted);font-size:10px}.metrics strong{display:block;font-size:19px;margin:7px 0;line-height:1.6}.wireframe{border:1px dashed #9caf9c;padding:15px;border-radius:12px;background:#f7f9f3}.status-chip{display:inline-block;border:1px solid var(--line);border-radius:18px;padding:3px 7px;font-size:10px;background:#edf2e6}
.diagram-wrap{overflow:auto;border:1px solid var(--line);border-radius:12px;padding:10px;background:#fcfdf8}.diagram-wrap svg{width:100%;display:block;min-width:760px}.diagram-wrap .wire{fill:none;stroke:#647b6e;stroke-width:2;marker-end:url(#d10-arrow-code)}.diagram-wrap .wire.next{stroke:#b27648;stroke-dasharray:7 5;marker-end:url(#d10-arrow-next)}.node rect{fill:#f5f8ef;stroke:#9db29f;stroke-width:1.5;rx:14px}.node.target rect,.node.graph rect{fill:#fbefe2;stroke:#b27648;stroke-dasharray:6 4}.node.source rect{fill:#e3eee1;stroke:#4d7157;stroke-width:2}.node text{font-size:12px;fill:#42594c}.node text.state{font-size:10px;fill:#75836d}.node text.name{font-size:18px;font-weight:750;fill:#244738}.edge-label{font-size:10px;fill:#75816e}.legend-text{font-size:12px;fill:#556c5a}
.chapter-footer{display:flex;justify-content:space-between;gap:10px;align-items:center;margin:23px 0 34px}.chapter-footer span{font-size:11px;color:var(--muted)}.chapter-footer button{font-size:12px}.single-footer{text-align:center;padding:25px;font-size:11px;color:var(--muted);border-top:1px solid var(--line)}.read-all .single-chapter{margin-bottom:45px}
@media(max-width:1000px){.single-layout{grid-template-columns:205px minmax(0,1fr);gap:16px;padding:0 18px 35px}.single-section{padding:22px}.grid.three,.metrics{grid-template-columns:1fr}.single-bar{padding:11px 18px}.grid{grid-template-columns:1fr}}
@media(max-width:760px){.single-brand span{font-size:16px}.single-brand small{font-size:8px}.single-bar{gap:8px}.top-tools{gap:4px}.top-tools button{padding:7px;font-size:10px}.version-pill{font-size:9px;padding:3px 6px}.hero-single{padding:24px 18px}.hero-single h1{letter-spacing:-1px}.single-layout{display:block}.single-nav{position:static;max-height:none;margin-bottom:18px}.chapter-grid-nav{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px}.chapter-sections{display:none}.chapter-entry{margin-bottom:0}.chapter-link{font-size:11px;padding:8px}.single-section{padding:20px 17px}.chapter-heading{padding:19px}.chapter-heading h2{font-size:25px}.single-section>h3{font-size:20px}.flow,.catalog,.wireframe-grid{grid-template-columns:1fr}.search-line button{font-size:10px;padding:7px}.search-line input{font-size:12px}.single-main{padding-top:0}}
@media print{.single-bar,.single-nav,.search-shell,.chapter-footer{display:none!important}.single-layout{display:block;padding:0}.single-chapter[hidden]{display:block!important}.single-chapter{break-before:page}.single-section{padding:10px 0;border:0;border-radius:0}.hero-single{padding:0 0 20px}.single-section table{min-width:0;font-size:9px}.table-wrap{overflow:visible}.diagram-wrap svg{min-width:0}.flow{grid-template-columns:repeat(3,1fr)}body{background:white}.single-footer{font-size:9px}}
'''

CSS += '''
.meeting-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:23px 0}.meeting-card{padding:18px;border:1px solid var(--line);border-radius:13px;background:#f2f6e9}.meeting-card>span{display:block;font-size:10px;font-weight:800;letter-spacing:.4px;color:#5a705a}.meeting-card h4,.meeting-card h5{margin:10px 0 12px;font-size:20px;line-height:1.4}.meeting-card p{font-size:12px;line-height:1.8}.meeting-card a{font-size:11px;font-weight:700}.meeting-card.direction{background:#edf1ed;border-color:#b7c5ba}.meeting-card.decide{background:#fbf0da;border-color:#d7bd84}.meeting-card.decide>span{color:#886426}.meeting-quick{display:flex;flex-wrap:wrap;gap:8px;margin-top:15px}.meeting-quick a{text-decoration:none;border:1px solid #b5c4ab;border-radius:9px;padding:7px 12px;background:#e9efdf;font-size:12px;font-weight:700}.meeting-quick a:last-child{background:#f7eace;border-color:#d3b473}.meeting-view .hero-single{padding-top:26px;padding-bottom:22px}.meeting-view .hero-single h1{font-size:clamp(30px,3.8vw,43px);letter-spacing:-1.5px}.meeting-view #d00>.chapter-heading{background:#e6ecd9}.meeting-view #d00-decisions th{background:#f3e3c4}.meeting-graph{margin:22px 0;border:1px solid var(--line);border-radius:12px;overflow:auto;padding:12px;background:#fcfdf8}.meeting-graph svg{display:block;width:100%;min-width:650px}.meeting-node rect{fill:#edf2e6;stroke:#a7b79f;stroke-width:1.5;rx:12px}.meeting-node.focus rect{fill:#dfead3;stroke:#6d885f;stroke-width:2}.meeting-node.proposal rect{fill:#faf0da;stroke:#c6ac74;stroke-dasharray:5 4}.meeting-node text{font-size:12px;fill:#52674b}.meeting-node text.kind{font-size:10px;fill:#75816a}.meeting-node text.title{font-size:17px;font-weight:800;fill:#284a39}.meeting-graph figcaption{font-size:11px;color:var(--muted);margin:5px 9px 2px}
@media(max-width:1000px){.meeting-summary{grid-template-columns:1fr}.meeting-card{padding:16px}.meeting-card h5{font-size:19px}}
@media(max-width:760px){.meeting-view .hero-single h1{font-size:30px}.meeting-quick a{font-size:11px;padding:7px 9px}}
'''

JS = r'''
(()=>{'use strict';const chapters=[...document.querySelectorAll('.single-chapter')],entries=[...document.querySelectorAll('.chapter-entry')],links=[...document.querySelectorAll('.chapter-link')],sections=[...document.querySelectorAll('.single-section')],allButton=document.getElementById('read-all'),q=document.getElementById('global-search'),results=document.getElementById('search-results'),count=document.getElementById('search-count');let active=0,all=false,timer,allDetails=null;
function activate(i,scroll=false){active=(i+chapters.length)%chapters.length;chapters.forEach((c,j)=>c.hidden=!all&&j!==active);entries.forEach((e,j)=>{e.classList.toggle('active',j===active);e.querySelector('.chapter-sections').hidden=j!==active;if(j===active)links[j].setAttribute('aria-current','page');else links[j].removeAttribute('aria-current')});document.getElementById('current-area').textContent=(active+1)+' / '+chapters.length;if(scroll)chapters[active].scrollIntoView({block:'start'})}
function openParents(el){let p=el;while(p){if(p.tagName==='DETAILS')p.open=true;p=p.parentElement}}
function route(){let id;try{id=decodeURIComponent(location.hash.slice(1))}catch{return}const el=document.getElementById(id);if(!el)return;const c=el.closest('.single-chapter');if(c){activate(chapters.indexOf(c));openParents(el);el.scrollIntoView({block:el===c||el.classList.contains('single-section')?'start':'center'})}}
function go(id){if(location.hash==='#'+id)route();else location.hash=id}
document.querySelectorAll('[data-chapter-step]').forEach(b=>b.onclick=()=>go(chapters[(active+Number(b.dataset.chapterStep)+chapters.length)%chapters.length].id));
allButton.onclick=()=>{all=!all;if(all){allDetails=[...document.querySelectorAll('details')].map(d=>[d,d.open]);allDetails.forEach(([d])=>d.open=true)}else if(allDetails){allDetails.forEach(([d,o])=>d.open=o);allDetails=null}document.body.classList.toggle('read-all',all);allButton.setAttribute('aria-pressed',String(all));allButton.textContent=all?'영역별 보기':'전체 펼쳐 보기';activate(active,true)};
const records=sections.map(s=>({el:s,title:s.querySelector('h3').textContent,chapter:s.closest('.single-chapter').querySelector('.chapter-heading h2').textContent,text:s.textContent.replace(/\s+/g,' ')}));
function search(){results.replaceChildren();const term=q.value.trim(),needle=term.toLocaleLowerCase();if(!needle){count.textContent='';return}let n=0;records.forEach(r=>{const pos=r.text.toLocaleLowerCase().indexOf(needle);if(pos<0)return;n++;const b=document.createElement('button');b.type='button';const title=document.createElement('b');title.textContent=r.chapter+' › '+r.title;const small=document.createElement('small'),start=Math.max(0,pos-35),end=Math.min(r.text.length,pos+term.length+90);small.append(document.createTextNode((start?'…':'')+r.text.slice(start,pos)));const mark=document.createElement('mark');mark.textContent=r.text.slice(pos,pos+term.length);small.append(mark,document.createTextNode(r.text.slice(pos+term.length,end)+(end<r.text.length?'…':'')));b.append(title,small);b.onclick=()=>{activate(chapters.indexOf(r.el.closest('.single-chapter')));r.el.querySelectorAll('details').forEach(d=>{if(d.textContent.toLocaleLowerCase().includes(needle))d.open=true});document.querySelectorAll('.search-focus').forEach(e=>e.classList.remove('search-focus'));const walk=document.createTreeWalker(r.el,NodeFilter.SHOW_TEXT,{acceptNode:n=>n.parentElement.closest('svg,script,style')?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_ACCEPT});let node,found;while(node=walk.nextNode()){if(node.textContent.toLocaleLowerCase().includes(needle)){found=node.parentElement;break}}const target=found||r.el;openParents(target);history.replaceState(null,'','#'+r.el.id);target.classList.add('search-focus');target.scrollIntoView({block:'center'})};results.append(b)});count.textContent=n+'개 절에서 찾았습니다. 결과를 누르면 해당 내용으로 이동합니다.'}
q.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(search,120)});q.addEventListener('keydown',e=>{if(e.key==='Escape')clear();if(e.key==='Enter'){clearTimeout(timer);search();results.querySelector('button')?.focus()}});function clear(){clearTimeout(timer);q.value='';results.replaceChildren();count.textContent='';document.querySelectorAll('.search-focus').forEach(e=>e.classList.remove('search-focus'));q.focus()}document.getElementById('clear-search').onclick=clear;
let printState;window.addEventListener('beforeprint',()=>{printState={details:[...document.querySelectorAll('details')].map(d=>[d,d.open])};chapters.forEach(c=>c.hidden=false);printState.details.forEach(([d])=>d.open=true)});window.addEventListener('afterprint',()=>{if(printState){printState.details.forEach(([d,o])=>d.open=o);activate(active)}});
activate(0);route();window.addEventListener('hashchange',route);
})();
'''


def render_single(docs,base,updates,plan):
    names={unicodedata.normalize('NFC',name):'d'+name[:2] for name in docs}
    records=[]
    meeting_path=plan/'content/meeting-20261030.json'
    meeting=json.loads(meeting_path.read_text()) if meeting_path.exists() else None
    if meeting:
        meeting_sections=[]
        for panel in meeting['sections']:
            parser=Fragment('00',names,plan);parser.feed(panel['html'])
            meeting_sections.append({'id':'d00-'+panel['id'],'title':panel['title'],'html':''.join(parser.parts)})
        records.append({'id':'d00','number':'회의','title':meeting['title'],'subtitle':meeting['subtitle'],'sections':meeting_sections})
    for name,doc in docs.items():
        sections=[]
        for panel in doc['panels']:
            parser=Fragment(name[:2],names,plan);parser.feed(panel['html'])
            sections.append({'id':'d'+name[:2]+'-'+panel['id'],'title':panel['title'],'html':''.join(parser.parts)})
        records.append({'id':'d'+name[:2],'number':name[:2],'title':doc['title'],'subtitle':doc['subtitle'],'sections':sections})
    parser=Fragment('12',names,plan);parser.feed(updates)
    records.append({'id':'d12','number':'12','title':'버전·출처·업데이트','subtitle':'통합본의 개정 기준과 원자료, 다음 업데이트 방법','sections':[{'id':'d12-updates','title':'공통 개정 기록','html':''.join(parser.parts)}]})
    nav=''.join('<div class="chapter-entry"><a class="chapter-link" href="#'+r['id']+'"><span class="number">'+r['number']+'</span><span>'+escape(r['title'])+'</span></a><div class="chapter-sections">'+''.join('<a href="#'+s['id']+'">'+escape(s['title'])+'</a>' for s in r['sections'])+'</div></div>' for r in records)
    body=''.join('<article class="single-chapter" id="'+r['id']+'" aria-labelledby="'+r['id']+'-heading"><header class="chapter-heading"><span class="eyebrow">CHAPTER '+r['number']+'</span><h2 id="'+r['id']+'-heading">'+escape(r['title'])+'</h2><p>'+escape(r['subtitle'])+'</p></header>'+''.join('<section class="single-section" id="'+s['id']+'" aria-labelledby="'+s['id']+'-heading"><h3 id="'+s['id']+'-heading">'+escape(s['title'])+'</h3>'+s['html']+'</section>' for s in r['sections'])+'<div class="chapter-footer"><button type="button" data-chapter-step="-1">← 이전 영역</button><span>'+str(chapter_index+1)+' / '+str(len(records))+'</span><button type="button" data-chapter-step="1">다음 영역 →</button></div></article>' for chapter_index,r in enumerate(records))
    metadata=json.dumps({'release':base['release'],'reviewed_date':base['reviewed_date'],'master_sha256':base['master_sha256'],'format':'single-file','source_documents':len(docs),'chapters':len(records),'sections':sum(len(r['sections']) for r in records),'source_panels':sum(len(d['panels']) for d in docs.values())},ensure_ascii=False).replace('</','<\\/')
    html = '''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="planning-release" content="'''+base['release']+'''"><title>Creative Checker · 통합 Planning</title><style>'''+CSS+'''</style></head><body><a class="skip" href="#main">본문으로 바로가기</a><header class="single-bar"><a class="single-brand" href="#d01"><small>CREATIVE CHECKER · SKN33 FINAL 1TEAM</small><span>통합 Planning</span></a><div class="top-tools"><span class="version-pill">'''+escape(base['release'])+'''</span><button type="button" id="read-all" aria-pressed="false">전체 펼쳐 보기</button></div></header><div class="hero-single"><span class="eyebrow">ONE FILE · PROJECT PLANNING</span><h1>프로젝트 문서를<br>한곳에서 확인합니다.</h1><p>기획·기능·화면·기술·권한·DB·모델·실험·아키텍처를 담은 통합본입니다.</p><div class="single-badges"><span>PostgreSQL 원천 + Neo4j 관계 조회</span><span>포스터 · 숏폼 · MCP</span><span>사용자 목표 반영 · 팀 승인 별도</span></div><div class="file-note">기준일 '''+base['reviewed_date']+''' KST · 최신 본문 11개 영역 + 공통 업데이트 · 이 파일 안에서 목차 이동과 전체 검색을 사용할 수 있습니다.</div></div><div class="single-layout"><nav class="single-nav" aria-label="통합 문서 목차"><p class="caption">CONTENTS · <span id="current-area">1 / 12</span></p><div class="chapter-grid-nav">'''+nav+'''</div></nav><main id="main" class="single-main"><div class="search-shell"><div class="search-line"><input type="search" id="global-search" aria-label="통합 Planning 전체 검색" placeholder="전체 문서 검색 · 예: Neo4j, 숏폼, 승인, F-02"><button type="button" id="clear-search">초기화</button></div><p class="search-count" id="search-count" role="status" aria-live="polite"></p><div class="search-list" id="search-results"></div></div>'''+body+'''</main></div><footer class="single-footer">Creative Checker · 통합 Planning · '''+escape(base['release'])+'''<br>본문·표·그림·스타일·검색 기능을 한 파일에 포함했습니다. 참고 소스 코드 링크는 원래 프로젝트 폴더에서 열립니다.</footer><noscript><p>JavaScript가 꺼져 있으면 모든 내용을 순서대로 읽을 수 있습니다.</p></noscript><script type="application/json" id="planning-metadata">'''+metadata+'''</script><script>'''+JS+'''</script></body></html>'''

    if meeting:
        html=html.replace('<title>Creative Checker · 통합 Planning</title>','<title>Creative Checker · 10/30 회의 공유안 · 통합 Planning</title>')
        html=html.replace('<body>','<body class="meeting-view">',1)
        html=html.replace('<a class="single-brand" href="#d01">','<a class="single-brand" href="#d00">')
        html=html.replace('ONE FILE · PROJECT PLANNING','MEETING BRIEF · 2026.10.30')
        html=html.replace('프로젝트 문서를<br>한곳에서 확인합니다.',escape(meeting['hero_title']).replace('&lt;br&gt;','<br>'))
        html=html.replace('기획·기능·화면·기술·권한·DB·모델·실험·아키텍처를 담은 통합본입니다.','통합문서 전체 요약과 GraphDB 조사 내용을 바탕으로 현재 진행 상황과 팀에서 결정할 안건을 정리했습니다.')
        html=html.replace('최신 본문 11개 영역 + 공통 업데이트','오늘 회의안 + 기존 Planning 11개 영역 + 공통 업데이트')
        html=html.replace('<div class="single-layout">','<div class="meeting-quick"><a href="#d00-status">현재 진행 상황</a><a href="#d00-graph">GraphDB 원리·활용</a><a href="#d00-decisions">회의에서 결정할 내용</a></div></div><div class="single-layout">',1)
        # The quick links belong inside the hero, not an extra wrapper.
        html=html.replace('</div></div><div class="meeting-quick">','</div><div class="meeting-quick">',1)
        html=html.replace('1 / 12','1 / '+str(len(records)))
    return html

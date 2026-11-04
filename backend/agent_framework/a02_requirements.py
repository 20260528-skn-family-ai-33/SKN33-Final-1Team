"""Compile validated Qwen source selections into conservative requirement drafts.

Only literal, source-backed forms are compiled. Unknown/compound meanings remain
manual. Explicit undecided source passages are retained with separate provenance;
this is an extraction policy, never a model fallback, invented value or approval.
"""
from dataclasses import dataclass
from hashlib import sha256
import re

from .a02_source_selection import UNRESOLVED_SOURCE
from .contracts import RequirementDetails,SourceRef
from .ports import AdapterFailure

QUOTED=re.compile(r'‘([^’]+)’|“([^”]+)”|「([^」]+)」|『([^』]+)』|"([^"\n]+)"')
QUALIFIER=re.compile(r'허용\s*범위\s*:')
DATE=re.compile(r'\d{4}년\s*\d{1,2}월\s*\d{1,2}일')
DIMENSION=re.compile(r'(?P<axis>가로|세로)\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>픽셀|px)\b',re.I)


@dataclass(frozen=True)
class CompiledRequirement:
    text:str
    kind:str
    operator:str
    expected:str
    unit:str | None
    required:bool
    block_id:str
    page:int
    quote:str
    unresolved:bool
    details:RequirementDetails


def reference(version,source,quote):
    return SourceRef(document_version_id=version,page=source.page,block_id=source.block_id,
        quote=quote,source_hash=sha256(quote.encode()).hexdigest())


def source_quote(item,source):
    quote=item.quote
    # A selected sentence may omit the next, explicitly labelled allowance.
    # Extend only that contiguous source passage; never interpret its meaning.
    offset=source.text.find(quote)
    if source.text.find(quote,offset+1)<0 and not QUALIFIER.search(quote):
        remainder=source.text[offset+len(quote):]
        match=re.match(r'\r?\n[ \t]*허용\s*범위\s*:[^\r\n]*',remainder)
        if match:
            quote+=match.group();tail=remainder[match.end():]
            while not quote.rstrip().endswith(('.', '。','!','?')):
                continuation=re.match(r'\r?\n([^\r\n]+)',tail)
                if continuation is None or re.match(r'\s*\d+(?:[.)]|\s*$)',continuation[1]):break
                quote+=continuation.group();tail=tail[continuation.end():]
                if len(quote)>4000:break
    if len(quote)>4000:
        raise AdapterFailure('A02_COMPILED_SOURCE_TOO_LARGE','A complete requirement excerpt exceeds the compilation limit.')
    return quote


def compile_selection(item,source,version):
    quote=source_quote(item,source)
    qualifier=QUALIFIER.search(quote)
    clause=quote[:qualifier.start()].rstrip() if qualifier else quote
    qualifiers=(reference(version,source,quote[qualifier.start():]),) if qualifier else (
        (reference(version,source,quote),) if re.search(r'허용|예외|또는|줄바꿈|공백',quote) else ())
    def make(target,field,value='',*,kind='text',operator='contains',unit=None,unresolved=False,manual=False):
        if manual or qualifiers or unresolved:operator='manual'
        details=RequirementDetails(target=target,field=field,
            expected_source=reference(version,source,value) if value else None,qualifiers=qualifiers)
        return CompiledRequirement(quote,kind,operator,value,unit,item.required,
            source.block_id,source.page,quote,unresolved,details)
    if item.unresolved:
        return [make('unspecified','unspecified',kind='unsupported',manual=True,unresolved=True)]
    if len(list(QUALIFIER.finditer(quote)))>1:
        return [make('unspecified','unspecified',kind='unsupported',manual=True)]
    dimensions=list(DIMENSION.finditer(clause))
    if dimensions:
        return [make('file_metadata','pixel_width' if m['axis']=='가로' else 'pixel_height',
            m['value'],kind='number',operator='equals',unit=m['unit']) for m in dimensions]
    delivery=re.search(r'(?:파일|결과물|산출물)\s*(?P<value>\d+)\s*(?P<unit>점|개|건)',clause)
    if delivery:
        return [make('delivery','quantity',delivery['value'],kind='number',unit=delivery['unit'],manual=True)]
    date=DATE.search(clause)
    if date:
        result=[make('poster_text','event_date',date.group(),manual=True)]
        weekday=re.search(r'[월화수목금토일]요일',clause)
        if weekday:result.append(make('poster_text','weekday',weekday.group()))
        return result
    if re.search(r'연도|년도',clause):
        year=re.search(r'(?<!\d)\d{4}(?!\d)',clause)
        if year:return [make('poster_text','year',year.group())]
    literals=[next(group for group in match.groups() if group is not None) for match in QUOTED.finditer(clause)]
    if literals and re.search(r'표기|기재|문구|제목|타이틀|전시명|행사명|공연명|장소|주소|연락처',clause):
        field=('title_phrase' if re.search(r'제목|타이틀|전시명|행사명|공연명',clause)
            else 'venue' if re.search(r'장소|공간|전시실',clause) else 'wording')
        if len(literals)==1 or re.search(r'모두|각각',clause):
            return [make('poster_text',field,value) for value in literals]
        # Alternatives/group semantics cannot be represented as one contains.
        return [make('poster_text',field,literals[0],manual=True)]
    # Table metadata requires its exact adjacent label as well as the value.
    offset=source.text.find(quote)
    unique=source.text.find(quote,offset+1)<0
    prefix=source.text[:offset].rstrip().splitlines()[-1:] if unique else []
    label=prefix[0].strip() if prefix else ''
    file_format=re.fullmatch(r'(JPEG|PNG|WEBP)(?:\s*\([^\n]*\))?',clause.strip(),re.I)
    if file_format and label in ('파일 형식','파일형식','납품 형식','납품형식'):
        return [make('file_metadata','file_format',file_format.group(1),operator='equals')]
    if clause.strip() in ('RGB','CMYK') and label in ('색상 모드','색상모드','컬러 모드','컬러모드'):
        return [make('file_metadata','color_mode',clause.strip(),operator='equals')]
    # Generic visual/general prose retains its complete instruction for review.
    visual=item.kind=='visual'
    return [make('poster_visual' if visual else 'unspecified','visual_requirements' if visual else 'unspecified',
        kind='visual' if visual else 'unsupported',manual=True)]


def unresolved_passages(source):
    lines=[];offset=0
    for raw in source.text.splitlines(keepends=True):
        lines.append((offset,offset+len(raw),raw));offset+=len(raw)
    marked=set()
    for match in UNRESOLVED_SOURCE.finditer(source.text):
        marked.update(i for i,(start,end,_) in enumerate(lines) if start<match.end() and end>match.start())
    groups=[]
    for number in sorted(marked):
        if groups and number<=groups[-1][-1]+1:groups[-1].append(number)
        else:groups.append([number])
    for group in groups:
        end=group[-1]
        # Keep short wrapped sentences intact. Do not consume the next section.
        while (end+1<len(lines) and not lines[end][2].rstrip().endswith(('.', '。','!','?'))
               and lines[end+1][2].strip() and not re.match(r'\s*\d+[.)]\s',lines[end+1][2])):
            end+=1
        quote=source.text[lines[group[0]][0]:lines[end][1]].rstrip('\r\n')
        if len(quote)>4000:
            raise AdapterFailure('A02_COMPILED_SOURCE_TOO_LARGE','An undecided source passage exceeds the compilation limit.')
        yield quote


def compile_requirements(candidates,sources,version):
    mapping={source.block_id:source for source in sources};result=[];selected={};seen=set()
    def append(item):
        key=(item.block_id,item.quote,item.details.target,item.details.field,item.expected,item.unit,item.required,item.unresolved)
        if key not in seen:result.append(item);seen.add(key)
        if len(result)>128:
            raise AdapterFailure('A02_COMPILED_CRITERIA_LIMIT','The compiled draft exceeds its explicit requirement limit.')
    for item in candidates:
        key=(item.block_id,item.quote)
        status=(item.required,item.unresolved)
        if key in selected:
            if selected[key]!=status:
                raise AdapterFailure('A02_SOURCE_SELECTION_CONFLICT','Repeated source selections disagree on their requirement state.')
            continue
        selected[key]=status
        # Known undecided passages are handled once, with source-marker lineage.
        if UNRESOLVED_SOURCE.search(item.quote):continue
        for compiled in compile_selection(item,mapping[item.block_id],version):append(compiled)
    retained=False
    for source in sources:
        for quote in unresolved_passages(source):
            details=RequirementDetails(target='unspecified',field='unspecified',origin='retained_unresolved_source')
            append(CompiledRequirement(quote,'unsupported','manual','',None,True,source.block_id,source.page,quote,True,details))
            retained=True
    if not result:
        raise AdapterFailure('A02_COMPILED_DRAFT_EMPTY','No source-grounded requirement draft could be compiled.')
    warnings=('explicit_unresolved_source_retained_by_policy',) if retained else ()
    return tuple(result),warnings

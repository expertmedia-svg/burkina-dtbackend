"""Grounded translation, independent of HTTP and model provider.

No model score or external dictionary automatically confers human validation.
The optional corpus contains reviewed bilingual sentences, not generated seeds.
"""
import hashlib
import json
import os
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

LANGUAGES = {"moore": "Mooré", "dioula": "Dioula", "fulfulde": "Fulfuldé"}
WIKI_CODES = {"moore": "mos", "dioula": "dyu", "fulfulde": "ff"}
STORE_LOCK = threading.RLock()
STOP = set("le la les un une des de du au aux je tu il elle nous vous ils elles et en a est ce que pour dans avec".split())
FORMS = {"veux": "vouloir", "veut": "vouloir", "voulons": "vouloir",
         "voudrais": "vouloir", "voudrait": "vouloir", "voudrions": "vouloir",
         "voudriez": "vouloir", "voudraient": "vouloir", "voulez": "vouloir",
         "vais": "aller", "va": "aller", "allons": "aller", "allez": "aller",
         "suis": "être", "sommes": "être", "êtes": "être", "sont": "être",
         "ai": "avoir", "as": "avoir", "avons": "avoir", "ont": "avoir",
         "fait": "faire", "fais": "faire", "faites": "faire"}


def normalize(text):
    # Preserve local-language diacritics; accents are not interchangeable sounds.
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFC", str(text)).casefold()))


def terms(text):
    return set(normalize(text).split()) - STOP


def french_variants(word):
    variants = {word, FORMS.get(word, word)}
    if len(word) > 3 and word.endswith(("s", "x")):
        variants.add(word[:-1])
    for suffix in ("ons", "ez", "ent", "es", "e", "ais", "ait", "aient", "é", "és", "ée", "ées"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            stem = word[:-len(suffix)]
            variants.add(stem + "er")
            if stem.endswith('e'):
                variants.add(stem + 'r')
    return variants


def usable(entry):
    return not (isinstance(entry, dict) and entry.get("source") == "ai_auto_enrichment"
                and entry.get("validated") is not True)


def as_entry(entry):
    return dict(entry) if isinstance(entry, dict) else {"translation": str(entry), "validated": False}


def merged_dictionary(dictionaries, custom, lang):
    merged = {**dictionaries.get(lang, {}), **custom.get(lang, {})}
    return {k: as_entry(v) for k, v in merged.items() if usable(v)}


def ranked_entries(text, dictionary, reverse=False, limit=40):
    query = terms(text)
    expanded = set(query)
    if not reverse:
        for word in query:
            expanded.update(french_variants(word))
    ranked = []
    for key, entry in dictionary.items():
        source = entry.get("translation", "") if reverse else key
        fields = " ".join(str(entry.get(f, "")) for f in
                          ("senses", "synonyms", "example_fr", "example_local"))
        score = 12 * len(query & terms(source)) + 7 * len(expanded & terms(source))
        score += 2 * len(expanded & terms(fields))
        if normalize(text) == normalize(source):
            score += 100
        if score:
            ranked.append((score, entry.get("validated") is True, key, entry))
    ranked.sort(key=lambda r: (-r[0], -int(r[1]), r[2]))
    return {key: entry for _, _, key, entry in ranked[:limit]}


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


@contextmanager
def proposal_lock(path):
    """Coordinate the threaded server and the separate human-review process."""
    with STORE_LOCK:
        lock_path = Path(str(path) + '.lock')
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open('a+b') as stream:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class ProposalStore:
    def __init__(self, path):
        self.path = Path(path)

    def add(self, source_lang, target_lang, text, translation, sources):
        identity = json.dumps([source_lang, target_lang, normalize(text), translation], ensure_ascii=False)
        identifier = hashlib.sha256(identity.encode()).hexdigest()[:24]
        with proposal_lock(self.path):
            records = read_json(self.path, {})
            if identifier not in records:
                records[identifier] = {"id": identifier, "source_lang": source_lang,
                    "target_lang": target_lang, "text": text, "translation": translation,
                    "sources": sources, "status": "pending_human_validation",
                    "created_at": time.time()}
                write_json(self.path, records)
        return identifier


class WiktionaryLookup:
    """Fetch actual versioned entries from one fixed public source, in one request.

    Keep language-specific passages only. No arbitrary model-supplied URLs.
    Bounded cache includes misses; outages have a short retry interval.
    """
    def __init__(self, opener=None):
        self.opener = opener or urllib.request.urlopen
        self.cache = {}
        self.lock = threading.Lock()

    @staticmethod
    def extract(wikitext, lang, reverse):
        code = WIKI_CODES[lang]
        sections = re.split(r"(?m)^==\s*(?![=])", wikitext)
        wanted = code if reverse else "fr"
        selected = next((s for s in sections if re.match(
            r"\{\{langue\|\s*" + re.escape(wanted) + r"\s*\}\}", s.strip())), "")
        if reverse:
            # Definitions/examples only, not translations into unrelated languages.
            lines = [line for line in selected.splitlines() if line.startswith("#")]
        else:
            lines, sense = [], ""
            for line in selected.splitlines():
                if "{{trad-début" in line:
                    sense = line
                if re.search(r"\{\{trad[+\-]?\|\s*" + code + r"\s*\|", line):
                    lines.append(sense + "\n" + line)
        return "\n".join(lines)[:3500]

    def lookup(self, words, lang, reverse=False):
        words = list(dict.fromkeys(w for w in words if w and "|" not in w and len(w) <= 150))[:8]
        key = (tuple(words), lang, reverse)
        with self.lock:
            cached = self.cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        if not words:
            return [], "no_match"
        params = {"action": "query", "format": "json", "formatversion": "2",
                  "prop": "revisions", "rvprop": "ids|content", "rvslots": "main",
                  "redirects": "1", "titles": "|".join(words)}
        request = urllib.request.Request("https://fr.wiktionary.org/w/api.php?" + urllib.parse.urlencode(params),
            headers={"User-Agent": "BurkinaDict/1.1 (linguistic reference lookup)", "Accept": "application/json"})
        try:
            with self.opener(request, timeout=6) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError("Document too large")
                data = json.loads(raw)
            if "error" in data:
                raise ValueError("Reference service error")
            documents = []
            for page in data.get("query", {}).get("pages", []):
                revisions = page.get("revisions", [])
                if not revisions:
                    continue
                revision = revisions[0]
                excerpt = self.extract(revision.get("slots", {}).get("main", {}).get("content", ""), lang, reverse)
                if excerpt:
                    documents.append({"id": "wiki:" + str(revision["revid"]),
                        "title": "Wiktionnaire — " + page["title"],
                        "url": "https://fr.wiktionary.org/w/index.php?oldid=" + str(revision["revid"]),
                        "language": lang, "excerpt": excerpt, "validated": False,
                        "license": "CC BY-SA; consulter la page source et son historique"})
            result = documents, "available" if documents else "no_match"
        except (OSError, ValueError, KeyError, TypeError):
            result = [], "unavailable"
        with self.lock:
            if len(self.cache) >= 256:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (time.monotonic() + (60 if result[1] == "unavailable" else 3600), result)
        return result


class TranslationEngine:
    def __init__(self, dictionaries, corpus_path, proposals_path, ai_call, lookup=None, query_planner=None):
        self.dictionaries = dictionaries
        self.corpus_path = Path(corpus_path)
        self.proposals = ProposalStore(proposals_path)
        self.ai_call = ai_call
        self.lookup = lookup or WiktionaryLookup()
        self.query_planner = query_planner

    def corpus(self, lang):
        if not self.corpus_path.exists():
            return []
        rows = []
        for line in self.corpus_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if (row.get("language") == lang and row.get("validated") is True
                    and row.get("reviewer") and row.get("french") and row.get("local")):
                rows.append(row)
        return rows

    def translate(self, text, source_lang, target_lang, config):
        if not isinstance(text, str) or not text.strip() or len(text) > 3000:
            raise ValueError("Le texte doit contenir entre 1 et 3 000 caractères.")
        if not ((source_lang == "fr" and target_lang in LANGUAGES)
                or (target_lang == "fr" and source_lang in LANGUAGES)):
            raise ValueError("Choisissez le français et une langue locale prise en charge.")
        reverse = target_lang == "fr"
        lang = source_lang if reverse else target_lang
        dictionary = merged_dictionary(self.dictionaries, config.get("customDictionary", {}), lang)
        corpus = self.corpus(lang)
        base = {"success": True, "input": text, "original_input": text, "corrected_input": text,
                "source_lang": source_lang, "target_lang": target_lang, "phonetic": "",
                "syllables": "", "vocal_reading": "", "example": "",
                "rules_applied": [], "synonyms_used": [], "sources": [], "missing_terms": [],
                "confidence": None, "confidence_type": "not_calibrated", "ai_processed": False}
        exact_corpus = [row for row in corpus if normalize(row["local" if reverse else "french"]) == normalize(text)]
        if exact_corpus and len({row["french" if reverse else "local"] for row in exact_corpus}) == 1:
                row = exact_corpus[0]
                return {**base, "translation": row["french" if reverse else "local"], "dialect": row.get('dialect', ''),
                        "source": "validated_corpus", "validation_status": "validated",
                        "sources": [{"title": row.get("source", "Corpus relu"), "url": row.get("url", "")}] + row.get('references', [])}
        exact = [(key, entry) for key, entry in dictionary.items()
                 if normalize(entry.get("translation", "") if reverse else key) == normalize(text)]
        # Ambiguous reverse entries must be disambiguated using context, never first-match.
        if len(exact) == 1:
            key, entry = exact[0]
            validated = entry.get("validated") is True
            return {**base, "translation": key if reverse else entry.get("translation", ""),
                    "phonetic": "" if reverse else entry.get("phonetic", ""),
                    "source": "local_dictionary", "validation_status": "validated" if validated else "pending_human_validation",
                    "warning": "" if validated else "Entrée du dictionnaire en attente de validation par un locuteur."}
        expansions = []
        if config.get('isAiEnabled') and self.query_planner:
            expansions = self.query_planner(text, source_lang, config)
            expansions = [word[:100] for word in expansions if isinstance(word, str)][:8] if isinstance(expansions, list) else []
        search_text = text + ' ' + ' '.join(expansions)
        subset = ranked_entries(text, dictionary, reverse)
        for key, entry in ranked_entries(search_text, dictionary, reverse).items():
            if len(subset) < 40:
                subset.setdefault(key, entry)
        examples = sorted(corpus, key=lambda row: len(terms(search_text) & terms(row["local" if reverse else "french"])), reverse=True)
        examples = [row for row in examples if terms(search_text) & terms(row["local" if reverse else "french"])][:8]
        known = set()
        for key, entry in subset.items():
            known.update(terms(entry.get("translation", "") if reverse else key))
        # This is lexical coverage only, never proof that the conjugated phrase
        # is translated. Preserve the original conditional/politeness in the input.
        mappings = {} if reverse else {word: FORMS[word] for word in normalize(text).split()
                                       if word in FORMS and FORMS[word] in known}
        missing = [word for word in normalize(text).split()
                   if word not in STOP and word not in known and word not in mappings]
        documents, research_status = [], "disabled"
        if config.get("isAiEnabled") and config.get("externalResearchEnabled", True):
            candidates = ([normalize(text)] if len(text) <= 150 else []) + missing[:4] + expansions
            if not reverse:
                for word in missing[:4]:
                    candidates.extend(sorted(french_variants(word) - {word}))
            documents, research_status = self.lookup.lookup(candidates, lang, reverse)
        context = {"dictionary": subset, "examples": examples,
            "rules": [r for r in config.get("rules", []) if r.get("language") == lang and r.get("isActive", True)],
            "documents": documents, "missing_dictionary_terms": missing,
            "source_lemma_matches": mappings,
            "retrieval_expansions_only": expansions,
            "research_status": research_status}
        result = None
        if config.get("isAiEnabled"):
            result = self.ai_call(text, target_lang, source_lang, LANGUAGES.get(target_lang, "Français"), config, dict_subset=context)
        if isinstance(result, dict) and isinstance(result.get("translation"), str) and result["translation"].strip():
            sources = [{k: doc[k] for k in ("id", "title", "url", "license")} for doc in documents]
            output = {**base, "translation": result["translation"].strip(),
                "corrected_input": result.get("corrected_input") if isinstance(result.get("corrected_input"), str) else text,
                "phonetic": result.get("phonetic", "") if isinstance(result.get("phonetic", ""), str) else "",
                "rules_applied": [r for r in result.get("rules_applied", []) if isinstance(r, str)] if isinstance(result.get("rules_applied"), list) else [],
                "source": "ai_with_references" if documents or examples else "ai_model",
                "ai_processed": True, "sources": sources, "research_status": research_status,
                "validation_status": "pending_human_validation",
                "warning": "Traduction proposée par l’IA, non validée par un locuteur.",
                "missing_terms": [w for w in result.get("missing_terms", []) if isinstance(w, str)] if isinstance(result.get("missing_terms"), list) else [],
                "dictionary_missing_terms": missing}
            if not output['corrected_input']:
                output['corrected_input'] = text
            if research_status == "unavailable":
                output["warning"] += " La recherche documentaire est temporairement indisponible."
            output["missing_terms"] = list(dict.fromkeys(output["missing_terms"] + re.findall(r'\[([^\]]+)\]', output['translation'])))
            if output["missing_terms"]:
                output["validation_status"] = "incomplete"
                output["warning"] += " Certains passages restent à traduire."
            # A queue failure must not discard an otherwise useful translation.
            try:
                output["proposal_id"] = self.proposals.add(source_lang, target_lang, text, output["translation"], sources)
            except (OSError, ValueError):
                output["proposal_saved"] = False
            return output
        # Conservative longest-phrase matching: retain every unmatched token,
        # including negation. No French grammar transformations on local input.
        tokens = re.findall(r"[^\W_]+|[^\w\s]", text, flags=re.UNICODE)
        index = {}
        for key, entry in dictionary.items():
            source = normalize(entry.get("translation", "") if reverse else key)
            index.setdefault(source, []).append(key if reverse else entry.get("translation", ""))
        pieces, unknown, pos = [], [], 0
        while pos < len(tokens):
            if not re.search(r"\w", tokens[pos]):
                pieces.append(tokens[pos]); pos += 1; continue
            for size in range(min(8, len(tokens) - pos), 0, -1):
                part = tokens[pos:pos + size]
                if any(not re.search(r"\w", token) for token in part):
                    continue
                values = index.get(normalize(" ".join(part)), [])
                if len(set(values)) == 1 and values[0]:
                    pieces.append(values[0]); pos += size; break
            else:
                unknown.append(tokens[pos]); pieces.append("[" + tokens[pos] + "]"); pos += 1
        return {**base, "translation": " ".join(pieces), "source": "local_rules_fallback",
                "validation_status": "incomplete" if unknown else "pending_human_validation",
                "missing_terms": unknown, "research_status": research_status,
                "warning": "Traduction locale mot à mot, à vérifier. Les passages entre crochets restent à traduire."}

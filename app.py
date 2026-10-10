import os, re, sqlite3, uuid, unicodedata, calendar, difflib, time
from datetime import date
from flask import Flask, g, render_template, request, redirect, url_for, send_from_directory, abort, session
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import seed

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "winxs.db")
UP = os.path.join(BASE, "uploads")
os.makedirs(UP, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 128 * 1024 * 1024

SITE = "TEAM WINXS DO FORRÓ"
MEDALHAS = [
    ("m_cura", "Maior Cura"),
    ("m_assist", "Maior Assistência"),
    ("m_defesa", "Maior Defesa"),
    ("m_dano", "Maior Dano"),
    ("m_kill", "Maior Kill"),
    ("triple", "Triple Kill"),
    ("penta", "Penta Kill")
]
FUNCOES = ["TANK", "DPS", "SUPORTE", "FLEX"]
STATUS_AGENDA = ["Agendada", "Realizada", "Cancelada"]
RESULTADOS = ["Pendente", "Vitória", "Derrota", "Empate"]


# ---------- banco de dados ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def _close(_):
    d = g.pop("db", None)
    if d:
        d.close()


def q(sql, a=()):
    return db().execute(sql, a).fetchall()


def one(sql, a=()):
    r = q(sql, a)
    return r[0] if r else None


def ex(sql, a=()):
    c = db().execute(sql, a)
    db().commit()
    return c.lastrowid


def opts(cat):
    return q(
        "SELECT * FROM options WHERE categoria=? ORDER BY ordem,id",
        (cat,)
    )


app.jinja_env.globals["opts"] = opts


SCHEMA = """
CREATE TABLE IF NOT EXISTS options(
    id INTEGER PRIMARY KEY,
    categoria TEXT,
    valor TEXT,
    extra TEXT DEFAULT '',
    ordem INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS heroes(
    id INTEGER PRIMARY KEY,
    nome TEXT UNIQUE,
    funcao TEXT
);

CREATE TABLE IF NOT EXISTS players(
    id INTEGER PRIMARY KEY,
    nick TEXT,
    nome TEXT DEFAULT '',
    elo TEXT,
    divisao INTEGER DEFAULT 1,
    plataforma TEXT,
    status TEXT DEFAULT 'Line Up',
    funcao TEXT,
    capita INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS player_heroes(
    player_id INTEGER REFERENCES players(id) ON DELETE CASCADE,
    hero_id INTEGER REFERENCES heroes(id) ON DELETE CASCADE,
    PRIMARY KEY(player_id, hero_id)
);

CREATE TABLE IF NOT EXISTS scrims(
    id INTEGER PRIMARY KEY,
    adversario TEXT,
    elo_medio TEXT,
    resultado TEXT,
    placar TEXT DEFAULT '',
    formato TEXT,
    origem TEXT,
    mapas TEXT DEFAULT '',
    data TEXT,
    hora TEXT,
    hora_inicio TEXT DEFAULT '20:00',
    hora_fim TEXT DEFAULT '21:00',
    prints TEXT DEFAULT '',
    obs TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agenda(
    id INTEGER PRIMARY KEY,
    adversario TEXT,
    elo_medio TEXT,
    formato TEXT,
    origem TEXT,
    data TEXT,
    hora TEXT,
    hora_inicio TEXT DEFAULT '20:00',
    hora_fim TEXT DEFAULT '21:00',
    status TEXT DEFAULT 'Agendada',
    obs TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS performance(
    id INTEGER PRIMARY KEY,
    player_id INTEGER REFERENCES players(id) ON DELETE CASCADE,
    scrim_id INTEGER REFERENCES scrims(id) ON DELETE SET NULL,
    heroi TEXT,
    data TEXT,
    k INTEGER DEFAULT 0,
    d INTEGER DEFAULT 0,
    a INTEGER DEFAULT 0,
    tempo INTEGER DEFAULT 0,
    precisao REAL DEFAULT 0,
    m_cura INTEGER DEFAULT 0,
    m_assist INTEGER DEFAULT 0,
    m_defesa INTEGER DEFAULT 0,
    m_dano INTEGER DEFAULT 0,
    m_kill INTEGER DEFAULT 0,
    triple INTEGER DEFAULT 0,
    penta INTEGER DEFAULT 0,
    prints TEXT DEFAULT '',
    obs TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS config(
    chave TEXT PRIMARY KEY,
    valor TEXT
);
"""


def init_db():
    c = sqlite3.connect(DB)
    c.executescript(SCHEMA)

    if not c.execute("SELECT 1 FROM options").fetchone():
        for i, v in enumerate(seed.ELOS):
            c.execute(
                "INSERT INTO options(categoria,valor,ordem) VALUES('elo',?,?)",
                (v, i)
            )

        for i, v in enumerate(seed.FORMATOS):
            c.execute(
                "INSERT INTO options(categoria,valor,ordem) VALUES('formato',?,?)",
                (v, i)
            )

        for i, v in enumerate(seed.ORIGENS):
            c.execute(
                "INSERT INTO options(categoria,valor,ordem) VALUES('origem',?,?)",
                (v, i)
            )

        for i, v in enumerate(seed.PLATAFORMAS):
            c.execute(
                "INSERT INTO options(categoria,valor,ordem) VALUES('plataforma',?,?)",
                (v, i)
            )

        for modo, lst in seed.MAPAS.items():
            for m in lst:
                c.execute(
                    "INSERT INTO options(categoria,valor,extra) VALUES('mapa',?,?)",
                    (m, modo)
                )

        for f, lst in seed.HEROIS.items():
            for h in lst.split(","):
                c.execute(
                    "INSERT OR IGNORE INTO heroes(nome,funcao) VALUES(?,?)",
                    (h, f)
                )

        for p in seed.PLAYERS:
            pid = c.execute(
                """INSERT INTO players(
                    nick,nome,elo,divisao,plataforma,status,funcao,capita
                ) VALUES(?,?,?,?,?,?,?,?)""",
                p[:8]
            ).lastrowid

            for h in p[8].split(","):
                r = c.execute(
                    "SELECT id FROM heroes WHERE nome=?",
                    (h,)
                ).fetchone()

                if r:
                    c.execute(
                        "INSERT OR IGNORE INTO player_heroes VALUES(?,?)",
                        (pid, r[0])
                    )

    # Adiciona os campos novos sem apagar os registros existentes.
    for tabela in ("scrims", "agenda"):
        colunas = {
            row[1]
            for row in c.execute(
                f"PRAGMA table_info({tabela})"
            ).fetchall()
        }

        if "hora_inicio" not in colunas:
            c.execute(
                f"ALTER TABLE {tabela} "
                "ADD COLUMN hora_inicio TEXT DEFAULT '20:00'"
            )

        if "hora_fim" not in colunas:
            c.execute(
                f"ALTER TABLE {tabela} "
                "ADD COLUMN hora_fim TEXT DEFAULT '21:00'"
            )

        registros = c.execute(
            f"SELECT id, hora, hora_inicio, hora_fim FROM {tabela}"
        ).fetchall()

        for rid, hora, inicio, fim in registros:
            if hora and inicio in (None, "", "20:00") and fim in (None, "", "21:00"):
                antigo = hora.strip().split(" - ")[0].strip()
                convertido = None

                for fmt in ("%H:%M", "%I:%M %p", "%H:%M %p"):
                    try:
                        import datetime as _dt
                        convertido = _dt.datetime.strptime(
                            antigo, fmt
                        ).strftime("%H:%M")
                        break
                    except ValueError:
                        pass

                if convertido:
                    h, mm = map(int, convertido.split(":"))
                    fim_h = (h + 1) % 24

                    c.execute(
                        f"UPDATE {tabela} "
                        "SET hora_inicio=?, hora_fim=? WHERE id=?",
                        (convertido, f"{fim_h:02d}:{mm:02d}", rid)
                    )

    c.commit()
    c.close()


# ---------- utilidades ----------
@app.template_filter("slug")
def slug(s):
    s = unicodedata.normalize(
        "NFD", s or ""
    ).encode("ascii", "ignore").decode()

    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


HERO_DIR = os.path.join(BASE, "static", "herois")

# Nome do arquivo (sem extensão) -> outros nomes que o personagem pode ter no cadastro.
# Para adicionar um personagem novo: coloque a foto em static/herois e, se o nome
# cadastrado for diferente do nome do arquivo, inclua a variação aqui.
HERO_ALIASES = {
    "adam": ["adam-warlock"],
    "arqueiro": ["hawkeye", "gaviao-arqueiro"],
    "blackcat": ["black-cat", "gata-negra"],
    "capitaoamerica": ["captain-america", "capitao-america"],
    "cavaleirodalua": ["moon-knight", "cavaleiro-da-lua"],
    "ciclope": ["cyclops"],
    "demolidor": ["daredevil"],
    "dino": ["moon-girl", "devil-dinosaur", "dinossauro", "garota-lunar",
             "lunar-girl", "dino-demonio", "moon-girl-e-dino-demonio",
             "moon-girl-and-devil-dinosaur"],
    "doutorestranho": ["doctor-strange", "doutor-estranho"],
    "elsa": ["elsa-bloodstone"],
    "emma": ["emma-frost"],
    "homemdeferro": ["iron-man", "homem-de-ferro"],
    "jeff": ["jeff-the-land-shark", "jeff-o-tubarao"],
    "luna": ["luna-snow"],
    "mantoeadaga": ["cloak-and-dagger", "cloak-dagger", "manto-e-adaga", "manto-adaga"],
    "misterfantastico": ["mister-fantastic", "mr-fantastic", "mr-fantastico", "senhor-fantastico"],
    "mulherinvisivel": ["invisible-woman", "mulher-invisivel"],
    "panteranegra": ["black-panther", "pantera-negra"],
    "psylocke": ["psyloque", "psilocque", "psilocke", "betsy-braddock"],
    "penny": ["peni-parker", "penny-parker", "peni"],
    "phoenix": ["jean-grey", "fenix"],
    "punhodeferro": ["iron-fist", "punho-de-ferro"],
    "rocket": ["rocket-raccoon", "rocket-guaxinim"],
    "tochahumana": ["human-torch", "tocha-humana"],
    "vampira": ["rogue"],
    "viuvanegra": ["black-widow", "viuva-negra"],
    "wanda": ["scarlet-witch", "feiticeira-escarlate", "wanda-maximoff"],
    "bucky": ["bucky-barnes", "winter-soldier", "soldado-invernal"],
    "raposabranca": ["white-fox", "raposa-branca"],
    "coisa": ["the-thing", "thing", "a-coisa"],
    "tempestade": ["storm"],
    "justiceiro": ["punisher", "o-justiceiro"],
    "capuz": ["hood", "o-capuz"],
    "senhordasestrelas": ["star-lord", "starlord", "senhor-das-estrelas"],
    "esquilo": ["squirrel-girl", "garota-esquilo", "esquilo"],
    "homemaranha": ["spider-man", "spiderman", "homem-aranha"],
}


def hero_img(nome):
    """URL da foto do personagem em static/herois, ou None se não houver."""
    s = slug(nome)
    if not s or not os.path.isdir(HERO_DIR):
        return None

    files = {}
    for f in os.listdir(HERO_DIR):
        stem, ext = os.path.splitext(f)
        if ext.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            files[slug(stem).replace("-", "")] = f

    juntos = s.replace("-", "")
    tokens = s.split("-")
    achado = files.get(juntos)

    if not achado:
        for key, f in files.items():
            nomes = {a.replace("-", "") for a in HERO_ALIASES.get(key, [])}
            if key in tokens or juntos in nomes:
                achado = f
                break

    if not achado and len(juntos) >= 4:
        # tolera pequenos erros de digitação no cadastro (ex.: "Psylock", "Psilocke")
        perto = difflib.get_close_matches(juntos, list(files), n=1, cutoff=0.8)
        if perto:
            achado = files[perto[0]]

    return url_for("static", filename=f"herois/{achado}") if achado else None


app.jinja_env.globals["hero_img"] = hero_img


@app.template_filter("br")
def br(d):
    return "/".join(reversed(d.split("-"))) if d and "-" in d else (d or "")


@app.template_filter("tempo")
def tempo(m):
    m = int(m or 0)
    return f"{m//60}h {m%60:02d}min" if m >= 60 else f"{m} min"


@app.template_filter("kda")
def kda(r):
    k, d, a = (r["k"] or 0), (r["d"] or 0), (r["a"] or 0)
    return f"{(k + a) / max(d, 1):.2f}"


@app.context_processor
def inject():
    return dict(
        SITE=SITE,
        opts=opts,
        MEDALHAS=MEDALHAS,
        FUNCOES=FUNCOES,
        STATUS_AGENDA=STATUS_AGENDA,
        RESULTADOS=RESULTADOS,
        hoje=date.today().isoformat()
    )


def get_times():
    f = request.form
    inicio = f.get("hora_inicio", "20:00")
    fim = f.get("hora_fim", "21:00")
    return inicio, fim, f"{inicio} - {fim}"


def get_data():
    """Monta YYYY-MM-DD a partir dos selects dia/mês/ano."""
    f = request.form
    if f.get("data"):
        return f["data"]
    ano = to_int(f.get("ano"), date.today().year)
    mes = min(max(to_int(f.get("mes"), 1), 1), 12)
    dia = to_int(f.get("dia"), 1)
    dia = min(max(dia, 1), calendar.monthrange(ano, mes)[1])
    return f"{ano:04d}-{mes:02d}-{dia:02d}"


def save_prints(old=""):
    keep = [
        n for n in old.split(",")
        if n and n not in request.form.getlist("rm")
    ]

    for f in request.files.getlist("prints"):
        if f and f.filename:
            n = f"{uuid.uuid4().hex[:8]}_{secure_filename(f.filename)}"
            f.save(os.path.join(UP, n))
            keep.append(n)

    return ",".join(keep)


def to_int(v, d=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


@app.route("/uploads/<path:f>")
def uploaded(f):
    return send_from_directory(UP, f)


# ---------- início ----------
@app.route("/")
def index():
    s = q("SELECT resultado, COUNT(*) n FROM scrims GROUP BY resultado")
    cnt = {r["resultado"]: r["n"] for r in s}
    tot = sum(cnt.values())
    v, dd = cnt.get("Vitória", 0), cnt.get("Derrota", 0)

    prox = q(
        """SELECT * FROM agenda
        WHERE status='Agendada' AND data>=?
        ORDER BY data,id LIMIT 4""",
        (date.today().isoformat(),)
    )

    ult = q("SELECT * FROM scrims ORDER BY data DESC,id DESC LIMIT 5")
    lineup = q(
        "SELECT * FROM players WHERE status='Line Up' ORDER BY id"
    )

    return render_template(
        "index.html",
        tot=tot,
        v=v,
        d=dd,
        wr=round(100 * v / max(v + dd, 1)),
        prox=prox,
        ult=ult,
        lineup=lineup
    )


# ---------- scrims ----------
@app.route("/scrims", methods=["GET", "POST"])
def scrims():
    if request.method == "POST":
        f, sid = request.form, request.form.get("id")
        old = one(
            "SELECT prints FROM scrims WHERE id=?",
            (sid,)
        ) if sid else None

        inicio, fim, hora_exibicao = get_times()

        vals = (
            f["adversario"],
            f["elo_medio"],
            f["resultado"],
            f.get("placar", ""),
            f["formato"],
            f["origem"],
            ",".join(f.getlist("mapas")),
            get_data(),
            hora_exibicao,
            inicio,
            fim,
            save_prints(old["prints"] if old else ""),
            f.get("obs", "")
        )

        cols = (
            "adversario,elo_medio,resultado,placar,formato,origem,"
            "mapas,data,hora,hora_inicio,hora_fim,prints,obs"
        )

        if sid:
            ex(
                "UPDATE scrims SET "
                + ",".join(c + "=?" for c in cols.split(","))
                + " WHERE id=?",
                vals + (sid,)
            )
        else:
            ex(
                f"INSERT INTO scrims({cols}) "
                f"VALUES({','.join('?' * 13)})",
                vals
            )

        return redirect(url_for("scrims"))

    fo = request.args.get("origem", "")
    fr = request.args.get("resultado", "")
    sql, a = "SELECT * FROM scrims WHERE 1=1", []

    if fo:
        sql += " AND origem=?"
        a.append(fo)

    if fr:
        sql += " AND resultado=?"
        a.append(fr)

    rows = q(sql + " ORDER BY data DESC,id DESC", a)

    edit = one(
        "SELECT * FROM scrims WHERE id=?",
        (request.args.get("edit"),)
    ) if request.args.get("edit") else None

    modos = [
        r["extra"]
        for r in q(
            """SELECT extra FROM options
            WHERE categoria='mapa'
            GROUP BY extra ORDER BY MIN(id)"""
        )
    ]

    return render_template(
        "scrims.html",
        rows=rows,
        edit=edit,
        modos=modos,
        fo=fo,
        fr=fr
    )


# ---------- agenda ----------
@app.route("/agenda", methods=["GET", "POST"])
def agenda():
    if request.method == "POST":
        f, i = request.form, request.form.get("id")
        inicio, fim, hora_exibicao = get_times()

        vals = (
            f["adversario"],
            f["elo_medio"],
            f["formato"],
            f["origem"],
            get_data(),
            hora_exibicao,
            inicio,
            fim,
            f["status"],
            f.get("obs", "")
        )

        cols = (
            "adversario,elo_medio,formato,origem,data,hora,"
            "hora_inicio,hora_fim,status,obs"
        )

        if i:
            ex(
                "UPDATE agenda SET "
                + ",".join(c + "=?" for c in cols.split(","))
                + " WHERE id=?",
                vals + (i,)
            )
        else:
            ex(
                f"INSERT INTO agenda({cols}) "
                f"VALUES({','.join('?' * 10)})",
                vals
            )

        return redirect(url_for("agenda"))

    rows = q(
        """SELECT * FROM agenda
        ORDER BY (status!='Agendada'),
        CASE WHEN status='Agendada' THEN data END ASC,
        data DESC"""
    )

    edit = one(
        "SELECT * FROM agenda WHERE id=?",
        (request.args.get("edit"),)
    ) if request.args.get("edit") else None

    return render_template("agenda.html", rows=rows, edit=edit)


@app.route("/agenda/<int:i>/virar", methods=["POST"])
def virar(i):
    a = one("SELECT * FROM agenda WHERE id=?", (i,))

    if a:
        ex(
            """INSERT INTO scrims(
                adversario,elo_medio,resultado,formato,origem,data,
                hora,hora_inicio,hora_fim,obs
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                a["adversario"],
                a["elo_medio"],
                "Pendente",
                a["formato"],
                a["origem"],
                a["data"],
                a["hora"],
                a["hora_inicio"],
                a["hora_fim"],
                a["obs"]
            )
        )

        ex(
            "UPDATE agenda SET status='Realizada' WHERE id=?",
            (i,)
        )

    return redirect(url_for("scrims"))


# ---------- players ----------
@app.route("/players", methods=["GET", "POST"])
def players():
    if request.method == "POST":
        f, i = request.form, request.form.get("id")

        vals = (
            f["nick"],
            f.get("nome", ""),
            f["elo"],
            to_int(f["divisao"], 1),
            f["plataforma"],
            f["status"],
            f["funcao"],
            1 if f.get("capita") else 0
        )

        if i:
            ex(
                """UPDATE players
                SET nick=?,nome=?,elo=?,divisao=?,plataforma=?,
                status=?,funcao=?,capita=? WHERE id=?""",
                vals + (i,)
            )
        else:
            i = ex(
                """INSERT INTO players(
                    nick,nome,elo,divisao,plataforma,status,funcao,capita
                ) VALUES(?,?,?,?,?,?,?,?)""",
                vals
            )

        ex("DELETE FROM player_heroes WHERE player_id=?", (i,))

        for h in f.getlist("herois"):
            ex(
                "INSERT OR IGNORE INTO player_heroes VALUES(?,?)",
                (i, h)
            )

        return redirect(url_for("players"))

    ps = q(
        """SELECT * FROM players
        ORDER BY (status!='Line Up'), id"""
    )

    pool = {
        p["id"]: q(
            """SELECT h.* FROM heroes h
            JOIN player_heroes ph ON ph.hero_id=h.id
            WHERE ph.player_id=? ORDER BY h.funcao,h.nome""",
            (p["id"],)
        )
        for p in ps
    }

    edit = one(
        "SELECT * FROM players WHERE id=?",
        (request.args.get("edit"),)
    ) if request.args.get("edit") else None

    sel = {
        r["hero_id"]
        for r in q(
            "SELECT hero_id FROM player_heroes WHERE player_id=?",
            (edit["id"],)
        )
    } if edit else set()

    return render_template(
        "players.html",
        ps=ps,
        pool=pool,
        edit=edit,
        sel=sel,
        heroes=q("SELECT * FROM heroes ORDER BY funcao,nome")
    )


@app.route("/players/<int:i>/elo", methods=["POST"])
def player_elo(i):
    ex(
        "UPDATE players SET elo=?,divisao=? WHERE id=?",
        (request.form["elo"], to_int(request.form["divisao"], 1), i)
    )
    return redirect(request.referrer or url_for("players"))


@app.route("/players/<int:i>/status", methods=["POST"])
def player_status(i):
    st = request.form.get("status", "")
    if st in ("Line Up", "Reserva"):
        ex("UPDATE players SET status=? WHERE id=?", (st, i))
    return redirect(request.referrer or url_for("players"))


# ---------- personagens ----------
@app.route("/personagens", methods=["GET", "POST"])
def personagens():
    if request.method == "POST":
        n = request.form["nome"].strip()

        if n:
            db().execute(
                "INSERT OR IGNORE INTO heroes(nome,funcao) VALUES(?,?)",
                (n, request.form["funcao"])
            )
            db().commit()

        return redirect(url_for("personagens"))

    hs = q("SELECT * FROM heroes ORDER BY nome")

    return render_template(
        "personagens.html",
        cols={
            f: [h for h in hs if h["funcao"] == f]
            for f in ["TANK", "DPS", "SUPORTE"]
        }
    )


@app.route("/personagens/<int:i>/funcao", methods=["POST"])
def hero_role(i):
    ex(
        "UPDATE heroes SET funcao=? WHERE id=?",
        (request.form["funcao"], i)
    )
    return redirect(url_for("personagens"))


# ---------- desempenho ----------
@app.route("/desempenho")
def desempenho():
    rows = q(
        """SELECT p.*, COUNT(f.id) n,
        COALESCE(SUM(f.k),0) k,
        COALESCE(SUM(f.d),0) d,
        COALESCE(SUM(f.a),0) a,
        COALESCE(SUM(f.tempo),0) tempo,
        AVG(NULLIF(f.precisao,0)) prec,
        COALESCE(SUM(
            f.m_cura+f.m_assist+f.m_defesa+f.m_dano+
            f.m_kill+f.triple+f.penta
        ),0) medalhas
        FROM players p
        LEFT JOIN performance f ON f.player_id=p.id
        GROUP BY p.id
        ORDER BY (p.status!='Line Up'), p.id"""
    )

    return render_template("desempenho.html", rows=rows)


@app.route("/desempenho/<int:pid>", methods=["GET", "POST"])
def desempenho_player(pid):
    p = one(
        "SELECT * FROM players WHERE id=?",
        (pid,)
    ) or abort(404)

    if request.method == "POST":
        f, i = request.form, request.form.get("id")
        old = one(
            "SELECT prints FROM performance WHERE id=?",
            (i,)
        ) if i else None

        ints = [to_int(f.get(c)) for c, _ in MEDALHAS]

        vals = (
            f["heroi"],
            f.get("scrim_id") or None,
            f["data"],
            to_int(f.get("k")),
            to_int(f.get("d")),
            to_int(f.get("a")),
            to_int(f.get("tempo")),
            float((f.get("precisao") or "0").replace(",", ".")),
            *ints,
            save_prints(old["prints"] if old else ""),
            f.get("obs", "")
        )

        cols = (
            "heroi,scrim_id,data,k,d,a,tempo,precisao,m_cura,"
            "m_assist,m_defesa,m_dano,m_kill,triple,penta,prints,obs"
        )

        if i:
            ex(
                "UPDATE performance SET "
                + ",".join(c + "=?" for c in cols.split(","))
                + " WHERE id=?",
                vals + (i,)
            )
        else:
            ex(
                f"INSERT INTO performance(player_id,{cols}) "
                f"VALUES({','.join('?' * 18)})",
                (pid,) + vals
            )

        return redirect(url_for("desempenho_player", pid=pid))

    por_heroi = q(
        """SELECT f.heroi, h.funcao, COUNT(*) n,
        SUM(f.tempo) tempo, SUM(f.k) k, SUM(f.d) d, SUM(f.a) a,
        AVG(NULLIF(f.precisao,0)) prec
        FROM performance f
        LEFT JOIN heroes h ON h.nome=f.heroi
        WHERE f.player_id=?
        GROUP BY f.heroi ORDER BY tempo DESC""",
        (pid,)
    )

    tot = one(
        "SELECT "
        + ",".join(
            f"COALESCE(SUM({c}),0) {c}"
            for c, _ in MEDALHAS
        )
        + """,
        COALESCE(SUM(k),0) k,
        COALESCE(SUM(d),0) d,
        COALESCE(SUM(a),0) a,
        COALESCE(SUM(tempo),0) tempo,
        AVG(NULLIF(precisao,0)) prec,
        COUNT(*) n
        FROM performance WHERE player_id=?""",
        (pid,)
    )

    regs = q(
        """SELECT f.*, h.funcao, s.adversario
        FROM performance f
        LEFT JOIN heroes h ON h.nome=f.heroi
        LEFT JOIN scrims s ON s.id=f.scrim_id
        WHERE f.player_id=?
        ORDER BY f.data DESC, f.id DESC""",
        (pid,)
    )

    edit = one(
        "SELECT * FROM performance WHERE id=?",
        (request.args.get("edit"),)
    ) if request.args.get("edit") else None

    return render_template(
        "player_perf.html",
        p=p,
        por_heroi=por_heroi,
        tot=tot,
        regs=regs,
        edit=edit,
        heroes=q("SELECT * FROM heroes ORDER BY funcao,nome"),
        scrims=q("SELECT id,adversario,data FROM scrims ORDER BY data DESC")
    )


# ---------- opções ----------

# ---------- opções ----------
CATS = [
    ("elo", "Elos"),
    ("mapa", "Mapas"),
    ("formato", "Formatos (MD3, MD5…)"),
    ("origem", "Tipo de scrim"),
    ("plataforma", "Plataformas")
]


@app.route("/opcoes", methods=["GET", "POST"])
def opcoes():
    if request.method == "POST":
        acao = request.form.get("acao", "adicionar")
        categoria = request.form.get("categoria", "")
        valor = request.form.get("valor", "").strip()
        extra = request.form.get("extra", "").strip()

        if acao == "trocar":
            item_id = request.form.get("id")

            if item_id and valor:
                ex(
                    "UPDATE options SET valor=?, extra=? WHERE id=?",
                    (valor, extra, item_id)
                )

        elif acao == "adicionar":
            if valor and categoria in [c[0] for c in CATS]:
                n = one(
                    """SELECT COALESCE(MAX(ordem),0)+1 n
                    FROM options WHERE categoria=?""",
                    (categoria,)
                )["n"]

                ex(
                    """INSERT INTO options
                    (categoria, valor, extra, ordem)
                    VALUES (?, ?, ?, ?)""",
                    (categoria, valor, extra, n)
                )

        return redirect(url_for("opcoes"))

    return render_template(
        "opcoes.html",
        cats=CATS,
        modos=[
            r["extra"]
            for r in q(
                """SELECT extra FROM options
                WHERE categoria='mapa'
                GROUP BY extra ORDER BY MIN(id)"""
            )
        ]
    )



# ---------- excluir ----------
@app.route("/excluir/<t>/<int:i>", methods=["POST"])
def delete(t, i):
    if t not in {
        "scrims", "agenda", "players", "heroes", "performance", "options"
    }:
        abort(404)

    ex(f"DELETE FROM {t} WHERE id=?", (i,))
    return redirect(request.referrer or url_for("index"))


# ---------- login com senha ----------
# A chave que assina o login fica em secret.key (não compartilhe esse arquivo).
KEY_FILE = os.path.join(BASE, "secret.key")
if not os.path.exists(KEY_FILE):
    with open(KEY_FILE, "wb") as fh:
        fh.write(os.urandom(32))
with open(KEY_FILE, "rb") as fh:
    app.secret_key = fh.read()

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,  # fica logado por 30 dias
)

PAGINAS_ABERTAS = {"login", "criar_senha", "static"}
TENTATIVAS = {}  # ip -> (falhas, bloqueado_até)
MAX_FALHAS, BLOQUEIO_SEG = 5, 300


def senha_hash():
    r = one("SELECT valor FROM config WHERE chave='senha_hash'")
    return r["valor"] if r else None


def salvar_senha(nova):
    ex(
        "INSERT OR REPLACE INTO config(chave,valor) VALUES('senha_hash',?)",
        (generate_password_hash(nova),)
    )


def entrar():
    session.clear()
    session.permanent = True
    session["ok"] = True
    session["v"] = senha_hash()[-12:]


def logado():
    h = senha_hash()
    return bool(h) and session.get("ok") and session.get("v") == h[-12:]


def destino_seguro(prox):
    if prox.startswith("/") and not prox.startswith("//") and "\\" not in prox:
        return prox
    return url_for("index")


def ip_cliente():
    xff = request.headers.get("X-Forwarded-For", "")
    return xff.split(",")[-1].strip() if xff else (request.remote_addr or "?")


def acesso_local():
    return (
        request.remote_addr in ("127.0.0.1", "::1")
        and not request.headers.get("X-Forwarded-For")
        and not request.headers.get("CF-Connecting-IP")
    )


@app.before_request
def exigir_login():
    if request.endpoint is None or request.endpoint in PAGINAS_ABERTAS:
        return
    if logado():
        return
    if not senha_hash():
        return redirect(url_for("criar_senha"))
    return redirect(url_for("login", next=request.full_path.rstrip("?")))


@app.after_request
def sem_cache_nas_paginas(resp):
    if request.endpoint not in ("static", "uploaded"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/criar-senha", methods=["GET", "POST"])
def criar_senha():
    if senha_hash():
        return redirect(url_for("login"))
    if not acesso_local():
        return render_template("login.html", modo="bloqueado"), 403
    erro = None
    if request.method == "POST":
        nova, conf = request.form.get("senha", ""), request.form.get("confirma", "")
        if len(nova) < 6:
            erro = "A senha precisa ter pelo menos 6 caracteres."
        elif nova != conf:
            erro = "As duas senhas não são iguais."
        else:
            salvar_senha(nova)
            entrar()
            return redirect(url_for("index"))
    return render_template("login.html", modo="criar", erro=erro)


@app.route("/login", methods=["GET", "POST"])
def login():
    if not senha_hash():
        return redirect(url_for("criar_senha"))
    erro, prox = None, request.values.get("next", "")
    if request.method == "POST":
        k = ip_cliente()
        falhas, ate = TENTATIVAS.get(k, (0, 0))
        if ate > time.time():
            erro = "Muitas tentativas erradas. Aguarde alguns minutos e tente de novo."
        elif check_password_hash(senha_hash(), request.form.get("senha", "")):
            TENTATIVAS.pop(k, None)
            entrar()
            return redirect(destino_seguro(prox))
        else:
            falhas += 1
            TENTATIVAS[k] = (0, time.time() + BLOQUEIO_SEG) if falhas >= MAX_FALHAS else (falhas, 0)
            erro = "Senha incorreta."
    return render_template("login.html", modo="entrar", erro=erro, next=prox)


@app.route("/senha", methods=["GET", "POST"])
def trocar_senha():
    erro = ok = None
    if request.method == "POST":
        f = request.form
        if not check_password_hash(senha_hash(), f.get("atual", "")):
            erro = "A senha atual está incorreta."
        elif len(f.get("nova", "")) < 6:
            erro = "A nova senha precisa ter pelo menos 6 caracteres."
        elif f.get("nova") != f.get("confirma"):
            erro = "As duas senhas novas não são iguais."
        else:
            salvar_senha(f["nova"])
            entrar()
            ok = "Senha alterada."
    return render_template("login.html", modo="trocar", erro=erro, ok=ok)


@app.route("/sair", methods=["POST"])
def sair():
    session.clear()
    return redirect(url_for("login"))


init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
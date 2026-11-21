import { ExternalLink, Shield } from "lucide-react";
import { useEffect, useState } from "react";
import type { Entity } from "../types";

interface Club {
  name: string;
  start: number | null;
  end: number | null;
  logo: string | null;
}
interface PlayerMedia {
  title: string;
  url: string;
  description: string | null;
  photo: string | null;
  born: number | null;
  position: string | null;
  clubs: Club[];
}
interface TeamMedia {
  description?: string | null;
  extract?: string | null;
  url?: string | null;
  flag: string | null;
  crest: string | null;
}
type Media = { player?: PlayerMedia | null; team?: TeamMedia };

/** Photo, bio and club career (players) or flag, crest and summary (teams), from Wikipedia/Wikidata. */
export function EntityMedia({ entity }: { entity: Entity }) {
  const [media, setMedia] = useState<Media | null>(null);
  const [loading, setLoading] = useState(false);
  const relevant = entity.label === "Player" || entity.label === "Team";

  useEffect(() => {
    if (!relevant) return;
    let live = true;
    setMedia(null);
    setLoading(true);
    fetch(`/api/media/entity/${encodeURIComponent(entity.id)}`)
      .then((r) => (r.ok ? r.json() : {}))
      .then((m: Media) => live && setMedia(m))
      .catch(() => live && setMedia({}))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [entity.id, relevant]);

  if (!relevant) return null;
  if (loading) return <div className="hero hero--loading" aria-busy="true"><div className="skeleton skeleton--avatar" /><div className="skeleton skeleton--lines" /></div>;

  if (entity.label === "Player") {
    const p = media?.player;
    if (!p) return null;
    return (
      <>
        <div className="hero hero--player">
          {p.photo ? <img className="hero__photo" src={p.photo} alt={`Photo of ${p.title}`} /> : <div className="hero__photo hero__photo--empty" />}
          <div className="hero__text">
            {p.description && <p className="hero__desc">{p.description}</p>}
            <div className="hero__chips">
              {p.born && <span className="pill">Born {p.born}</span>}
              {p.position && <span className="pill">{p.position}</span>}
            </div>
            <a className="hero__link" href={p.url} target="_blank" rel="noreferrer">
              Wikipedia <ExternalLink size={12} />
            </a>
          </div>
        </div>
        {p.clubs.length > 0 && (
          <section className="section">
            <h3>Club career</h3>
            <ul className="clubs">
              {p.clubs.map((c) => (
                <li key={`${c.name}-${c.start}`}>
                  <ClubBadge club={c} />
                  <span className="clubs__name">{c.name}</span>
                  <span className="clubs__years mono">
                    {c.start ?? "?"}–{c.end ?? (c.start ? "now" : "?")}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}
        <p className="credit">Photo, bio &amp; clubs: Wikipedia / Wikidata · images from Wikimedia Commons (free licences)</p>
      </>
    );
  }

  const t = media?.team;
  if (!t || (!t.flag && !t.crest && !t.extract)) return null;
  return (
    <>
      <div className="hero hero--team">
        <div className="hero__banner" style={t.flag ? { backgroundImage: `url("${t.flag}")` } : undefined}>
          {t.crest && (
            <span className="hero__crest">
              <img src={t.crest} alt={`${entity.name} crest`} />
            </span>
          )}
        </div>
        {t.extract && <p className="hero__extract">{t.extract}</p>}
        {t.url && (
          <a className="hero__link" href={t.url} target="_blank" rel="noreferrer">
            Wikipedia <ExternalLink size={12} />
          </a>
        )}
      </div>
      <p className="credit">Flag, crest &amp; summary: Wikipedia / Wikimedia Commons (free licences)</p>
    </>
  );
}

function ClubBadge({ club }: { club: Club }) {
  const [broken, setBroken] = useState(false);
  if (club.logo && !broken) {
    return <img className="clubs__logo" src={club.logo} alt="" onError={() => setBroken(true)} />;
  }
  const initials = club.name
    .replace(/\b(FC|F\.C\.|CF|AC|SC|Club|de|Fútbol|Football|Calcio|S\.C\.|C\.F\.|E\.C\.)\b/gi, "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();
  return (
    <span className="clubs__logo clubs__logo--initials" aria-hidden>
      {initials || <Shield size={12} />}
    </span>
  );
}

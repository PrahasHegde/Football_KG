import { playerPhoto, teamMedia, useMediaVersion } from "../media";

/** A team's flag (or nothing while unknown). Re-renders when media arrives. */
export function Flag({ id, size = 16 }: { id: string; size?: number }) {
  useMediaVersion();
  const url = teamMedia(id)?.flag;
  if (!url) return null;
  return <img className="flag" src={url} alt="" width={Math.round(size * 1.45)} height={size} loading="lazy" />;
}

/** Flag for teams, photo for players (if already looked up), otherwise null. */
export function Avatar({ id, size = 22 }: { id: string; size?: number }) {
  useMediaVersion();
  if (id.startsWith("T:")) {
    const url = teamMedia(id)?.flag;
    return url ? <img className="avatar avatar--flag" src={url} alt="" width={size} height={size} /> : null;
  }
  const photo = playerPhoto(id);
  return photo ? <img className="avatar" src={photo} alt="" width={size} height={size} /> : null;
}

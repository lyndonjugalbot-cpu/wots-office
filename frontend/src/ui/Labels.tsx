import { deskLayout } from "../office/layout";
import { registerLabel } from "../office/labels";
import type { OfficeAgent } from "../types";

/** Name plates and speech bubbles for every desk, positioned by <LabelProjector> in the 3D scene. */
export function Labels({ agents, selected }: { agents: OfficeAgent[]; selected: string | null }) {
  const { spots } = deskLayout(agents);
  return (
    <div className="labels" aria-hidden>
      {agents.map((member) => {
        const spot = spots[member.id];
        if (!spot) return null;
        const [x, , z] = spot.position;
        return (
          <div key={member.id}>
            <div className="label" ref={(el) => registerLabel(`${member.id}:tag`, [x, 0.05, z + 1.55], el)}>
              <div className={`nametag ${selected === member.id ? "nametag--selected" : ""}`}>
                {member.name}
                <span>{member.title}</span>
              </div>
            </div>
            <div className="label label--bubble" ref={(el) => registerLabel(`${member.id}:bubble`, [x, 2.35, z], el)}>
              {member.bubble ? <div className={`bubble bubble--${member.status}`}>{member.bubble}</div> : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

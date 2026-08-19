import { NavLink } from "react-router-dom";

export default function Sidebar() {
    const item = (to, label) => (
    <NavLink to={to} end className={({ isActive }) => `nav${isActive ? " on" : ""}`}>
      {label}
    </NavLink>
  );
  return (
    <aside className="side">
      <div className="brand">
        <div className="mk">M</div>
        <div><b>Meridian</b><span>Claims intelligence</span></div>
      </div>
      {item("/", "Overview")}
      <div className="navlabel">Claims</div>
      {item("/claims", "Claims table")}
      <div className="navlabel">Providers</div>
      {item("/providers", "Providers table")}
      <div className="navlabel">Investigation</div>
      {item("/queue", "Investigator queue")}
      <div className="side-foot">
        Risk scores prioritise review.<br />They do not establish fraud.
      </div>
    </aside>
  );
}

import { NavLink, Outlet } from "react-router-dom";
import { Activity, Building2, FlaskConical, Gauge, LogIn, LogOut, Siren, SlidersHorizontal } from "lucide-react";
import { useAuth } from "../api/auth";

const links = [
  { to: "/", label: "Tổng quan", icon: Gauge, end: true },
  { to: "/stations/station-1/operator", label: "Trạm", icon: Building2, end: false },
  { to: "/incidents", label: "Sự cố", icon: Siren, end: false },
  { to: "/demo", label: "Điều khiển demo", icon: SlidersHorizontal, end: false },
  { to: "/evaluations", label: "Đánh giá", icon: FlaskConical, end: false },
];

export function Layout() {
  const { role, username, logout, error } = useAuth();
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <Activity size={20} aria-hidden /> ChargeOps
        </div>
        <nav aria-label="Điều hướng chính">
          {links.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => (isActive ? "active" : "")}>
              <Icon size={16} aria-hidden /> {label}
            </NavLink>
          ))}
        </nav>
        <div className="who">
          {role === "operator" ? (
            <>
              <span className="badge ok">Operator{username ? `: ${username}` : ""}</span>
              <button type="button" className="btn ghost" onClick={() => void logout()}>
                <LogOut size={14} aria-hidden /> Đăng xuất
              </button>
            </>
          ) : (
            <>
              <span className="badge neutral">Chỉ xem</span>
              <NavLink to="/login" className="btn ghost">
                <LogIn size={14} aria-hidden /> Đăng nhập operator
              </NavLink>
            </>
          )}
        </div>
      </header>
      {error && (
        <div className="banner" role="alert">
          Không kiểm tra được phiên đăng nhập: {error.message}. Giao diện đang ở chế độ chỉ xem.
        </div>
      )}
      <main>
        <Outlet />
      </main>
      <footer className="foot">Giờ hiển thị: Asia/Ho_Chi_Minh · Làm mới mỗi 5 giây · Mô phỏng demo, không điều khiển thiết bị thật</footer>
    </div>
  );
}

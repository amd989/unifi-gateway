Name:           unifi-gateway
Version:        %{_version}
Release:        1%{?dist}
Summary:        UniFi Gateway Emulator
License:        MIT
URL:            https://github.com/amd989/unifi-gateway
Source0:        unifi-gateway-%{version}.tar.gz

%global debug_package %{nil}

%description
A daemon that emulates a Ubiquiti UniFi Gateway (UGW3) to a UniFi
Controller, allowing non-Ubiquiti routers (OpenWRT, OPNSense, pfSense)
to report network stats to the UniFi Controller UI.

%prep
%setup -q

%build
# Binary is pre-built by PyInstaller

%install
install -Dm755 dist/unifi-gateway %{buildroot}%{_bindir}/unifi-gateway
install -Dm644 unifi-gateway.service %{buildroot}%{_unitdir}/unifi-gateway.service
install -Dm644 conf/unifi-gateway.sample.conf %{buildroot}%{_sysconfdir}/unifi-gateway/unifi-gateway.sample.conf

%files
%license LICENSE
%doc README.md
%{_bindir}/unifi-gateway
%{_unitdir}/unifi-gateway.service
%dir %{_sysconfdir}/unifi-gateway
%{_sysconfdir}/unifi-gateway/unifi-gateway.sample.conf

%post
if [ ! -f %{_sysconfdir}/unifi-gateway/unifi-gateway.conf ]; then
    cp %{_sysconfdir}/unifi-gateway/unifi-gateway.sample.conf %{_sysconfdir}/unifi-gateway/unifi-gateway.conf
    echo "Created %{_sysconfdir}/unifi-gateway/unifi-gateway.conf from sample — edit before starting!"
fi
%systemd_post unifi-gateway.service

%preun
%systemd_preun unifi-gateway.service

%postun
%systemd_postun_with_restart unifi-gateway.service

%changelog

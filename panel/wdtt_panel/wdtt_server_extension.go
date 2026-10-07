package main

import (
	"errors"
	"fmt"
	"log"
	"strconv"
	"strings"
	"time"

	"golang.zx2c4.com/wireguard/device"
)

const wdttPanelExtensionMarker = "wdtt-panel-extension-v10"

func normalizeUserLabel(value string) (string, error) {
	label := strings.TrimSpace(value)
	if label == "-" {
		return "", nil
	}
	if len([]rune(label)) > 64 {
		return "", errors.New("label is too long")
	}
	for _, char := range label {
		if char < 32 || char == 127 {
			return "", errors.New("label contains a control character")
		}
	}
	return label, nil
}

func telegramLabel(value string) string {
	return strings.NewReplacer("\\", "\\\\", "_", "\\_", "*", "\\*", "`", "\\`", "[", "\\[").Replace(value)
}

func trafficQuota(entry *PasswordEntry) (used, primary, extra, remaining int64, exhausted bool) {
	if entry == nil || !entry.TrafficManaged || entry.TrafficUnlimited {
		return 0, 0, 0, 0, false
	}
	used = entry.DownBytes + entry.UpBytes - entry.TrafficBaselineBytes
	if used < 0 {
		used = 0
	}
	primary = entry.TrafficPrimaryBytes - used
	if primary < 0 {
		primary = 0
	}
	extraUsed := used - entry.TrafficPrimaryBytes
	if extraUsed < 0 {
		extraUsed = 0
	}
	extra = entry.TrafficExtraBytes - extraUsed
	if extra < 0 {
		extra = 0
	}
	remaining = primary + extra
	return used, primary, extra, remaining, remaining <= 0
}

func trafficQuotaExhausted(entry *PasswordEntry) bool {
	_, _, _, _, exhausted := trafficQuota(entry)
	return exhausted
}

func passwordAccessRestricted(entry *PasswordEntry) bool {
	return entry == nil || isPasswordExpired(entry) || entry.IsDeactivated || trafficQuotaExhausted(entry)
}

func passwordForEntryLocked(target *PasswordEntry) string {
	for password, entry := range db.Passwords {
		if entry == target {
			return password
		}
	}
	return ""
}

func restrictPasswordEntryLocked(password string, entry *PasswordEntry, wgDev *device.Device) {
	if password != "" {
		disconnectCredentialConnections(password)
		serverWrapKeys.RemovePassword(password)
	}
	for _, deviceID := range entryDeviceIDs(entry) {
		if dev := db.Devices[deviceID]; dev != nil {
			removeClientSpeedLimits(wgIfaceName, dev.IP)
			removePeerFromWG(wgDev, dev)
		} else {
			removePeerFromWG(wgDev, db.Devices[deviceID])
		}
	}
}

func applyPasswordRestrictionsLocked(wgDev *device.Device) int {
	restricted := 0
	for password, entry := range db.Passwords {
		if passwordAccessRestricted(entry) {
			restrictPasswordEntryLocked(password, entry, wgDev)
			restricted++
		}
	}
	return restricted
}

func deviceAccessAllowedLocked(deviceID string, dev *ClientDevice) bool {
	entry := generatedOwnerEntryLocked(dev, deviceID)
	return entry == nil || !passwordAccessRestricted(entry)
}

func deviceUsesMainPasswordLocked(dev *ClientDevice) bool {
	if dev == nil || db.MainPassword == "" {
		return false
	}
	ownerID := wrapKeyID(db.MainPassword)
	return dev.OwnerID == ownerID || dev.RawOwnerID == ownerID
}

func recordPasswordTrafficLocked(entry *PasswordEntry, up, down int64) {
	entry.UpBytes += up
	entry.DownBytes += down
	now := time.Now().Unix()
	if up > 0 {
		entry.LastUploadAt = now
	}
	if down > 0 {
		entry.LastDownloadAt = now
	}
	if trafficQuotaExhausted(entry) {
		restrictPasswordEntryLocked(passwordForEntryLocked(entry), entry, globalWgDev)
	}
}

func recordMainTrafficLocked(up, down int64) {
	db.MainUpBytes += up
	db.MainDownBytes += down
	now := time.Now().Unix()
	if up > 0 {
		db.MainLastUploadAt = now
	}
	if down > 0 {
		db.MainLastDownloadAt = now
	}
}

// ==================== Лимиты скорости (tc HTB, панель 1.10.0) ====================

// mbpsToTcRate переводит Мбит/с из панели в rate-строку tc.
func mbpsToTcRate(mbps float64) string {
	if mbps <= 0 {
		return ""
	}
	kbit := int(mbps*1000.0 + 0.5)
	if kbit < 8 {
		kbit = 8
	}
	if kbit%1000 == 0 {
		return fmt.Sprintf("%dmbit", kbit/1000)
	}
	return fmt.Sprintf("%dkbit", kbit)
}

func speedLimitIPOctet(ip string) (int, bool) {
	parts := strings.Split(ip, ".")
	if len(parts) != 4 {
		return 0, false
	}
	n, err := strconv.Atoi(parts[3])
	if err != nil || n < 2 || n > 254 {
		return 0, false
	}
	return n, true
}

func tcNeedsHTBReset(qdiscShow string) bool {
	hasHTB := false
	hasOtherRoot := false
	for _, line := range strings.Split(qdiscShow, "\n") {
		line = strings.TrimSpace(line)
		if !strings.Contains(line, " root ") {
			continue
		}
		if strings.Contains(line, "qdisc htb 1:") {
			hasHTB = true
			continue
		}
		if strings.HasPrefix(line, "qdisc ") {
			hasOtherRoot = true
		}
	}
	return !hasHTB || hasOtherRoot
}

func ensureSpeedLimitHTB(iface string) {
	out, _ := runCmd("tc", "qdisc", "show", "dev", iface)
	if tcNeedsHTBReset(out) {
		runCmdSilent("tc", "qdisc", "del", "dev", iface, "root")
		if msg := runCmdSilent("tc", "qdisc", "add", "dev", iface, "root", "handle", "1:", "htb", "default", "999"); msg != "" {
			log.Printf("[TC] qdisc htb add %s: %s", iface, msg)
		}
	}
	runCmdSilent("tc", "class", "add", "dev", iface, "parent", "1:", "classid", "1:1", "htb", "rate", "10gbit")
	runCmdSilent("tc", "class", "add", "dev", iface, "parent", "1:1", "classid", "1:999", "htb", "rate", "10gbit", "ceil", "10gbit")
}

func ensureSpeedLimitIngress(iface string) {
	out, _ := runCmd("tc", "qdisc", "show", "dev", iface)
	if strings.Contains(out, "ingress") {
		return
	}
	if msg := runCmdSilent("tc", "qdisc", "add", "dev", iface, "handle", "ffff:", "ingress"); msg != "" {
		log.Printf("[TC] ingress add %s: %s", iface, msg)
	}
}

func tcSpeedFilterExists(iface, parent string, prio int) bool {
	out, _ := runCmd("tc", "filter", "show", "dev", iface, "parent", parent)
	return strings.Contains(out, fmt.Sprintf("pref %d ", prio)) ||
		strings.Contains(out, fmt.Sprintf("pref %d\n", prio)) ||
		strings.Contains(out, fmt.Sprintf("prio %d", prio))
}

func removeClientSpeedLimits(iface, ip string) {
	if iface == "" || ip == "" {
		return
	}
	octet, ok := speedLimitIPOctet(ip)
	if !ok {
		return
	}
	classID := fmt.Sprintf("1:%d", octet)
	runCmdSilent("tc", "filter", "del", "dev", iface, "protocol", "ip", "parent", "1:0", "prio", strconv.Itoa(octet))
	runCmdSilent("tc", "class", "del", "dev", iface, "classid", classID)
	runCmdSilent("tc", "filter", "del", "dev", iface, "protocol", "ip", "parent", "ffff:", "prio", strconv.Itoa(octet+1000))
}

// applyClientSpeedLimits ставит/снимает лимиты IP. Мбит/с <= 0 — снять направление.
// Вызывать под dbMutex (суффикс Unlocked у соседей — та же конвенция).
func applyClientSpeedLimits(iface, ip string, downMbps, upMbps float64) {
	if !commandExists("tc") {
		log.Printf("[TC] tc не найден — лимит скорости для %s не применён (установите iproute2)", ip)
		return
	}
	if downMbps <= 0 && upMbps <= 0 {
		removeClientSpeedLimits(iface, ip)
		return
	}
	octet, ok := speedLimitIPOctet(ip)
	if !ok {
		log.Printf("[TC] некорректный IP клиента: %s", ip)
		return
	}
	if downMbps > 0 {
		ensureSpeedLimitHTB(iface)
		rate := mbpsToTcRate(downMbps)
		classID := fmt.Sprintf("1:%d", octet)
		if out := runCmdSilent("tc", "class", "replace", "dev", iface, "parent", "1:1", "classid", classID,
			"htb", "rate", rate, "ceil", rate); out != "" {
			log.Printf("[TC] class replace %s: %s", ip, out)
		}
		if !tcSpeedFilterExists(iface, "1:", octet) {
			if out := runCmdSilent("tc", "filter", "add", "dev", iface, "protocol", "ip", "parent", "1:0",
				"prio", strconv.Itoa(octet), "u32", "match", "ip", "dst", ip+"/32", "flowid", classID); out != "" {
				log.Printf("[TC] filter dst %s: %s", ip, out)
			}
		}
		log.Printf("[TC] ↓ %s: %.2f Мбит/с (%s)", ip, downMbps, rate)
	} else {
		runCmdSilent("tc", "filter", "del", "dev", iface, "protocol", "ip", "parent", "1:0", "prio", strconv.Itoa(octet))
		runCmdSilent("tc", "class", "del", "dev", iface, "classid", fmt.Sprintf("1:%d", octet))
	}
	if upMbps > 0 {
		ensureSpeedLimitIngress(iface)
		rate := mbpsToTcRate(upMbps)
		prio := octet + 1000
		runCmdSilent("tc", "filter", "del", "dev", iface, "protocol", "ip", "parent", "ffff:", "prio", strconv.Itoa(prio))
		if out := runCmdSilent("tc", "filter", "add", "dev", iface, "parent", "ffff:", "protocol", "ip",
			"prio", strconv.Itoa(prio), "u32", "match", "ip", "src", ip+"/32",
			"police", "rate", rate, "burst", "32kb", "drop"); out != "" {
			log.Printf("[TC] filter src %s: %s", ip, out)
		} else {
			log.Printf("[TC] ↑ %s: %.2f Мбит/с (%s)", ip, upMbps, rate)
		}
	} else {
		runCmdSilent("tc", "filter", "del", "dev", iface, "protocol", "ip", "parent", "ffff:", "prio", strconv.Itoa(octet+1000))
	}
}

func applySpeedLimitForEntryUnlocked(entry *PasswordEntry) {
	if entry == nil {
		return
	}
	for _, deviceID := range entryDeviceIDs(entry) {
		dev := db.Devices[deviceID]
		if dev == nil || dev.IP == "" {
			continue
		}
		applyClientSpeedLimits(wgIfaceName, dev.IP, entry.MaxDownMbps, entry.MaxUpMbps)
	}
}

// syncAllSpeedLimits сбрасывает tc на wdtt0 и накатывает лимиты заново —
// после старта и перезагрузки БД. Без лимитов у активных записей qdisc не трогаем.
func syncAllSpeedLimits() {
	if !commandExists("tc") {
		log.Printf("[TC] tc не найден — установите iproute2 для лимитов скорости")
		return
	}
	dbMutex.Lock()
	defer dbMutex.Unlock()
	hasLimits := false
	for _, entry := range db.Passwords {
		if entry == nil || entry.IsDeactivated {
			continue
		}
		if entry.MaxDownMbps > 0 || entry.MaxUpMbps > 0 {
			hasLimits = true
			break
		}
	}
	if !hasLimits {
		return
	}
	runCmdSilent("tc", "qdisc", "del", "dev", wgIfaceName, "root")
	runCmdSilent("tc", "qdisc", "del", "dev", wgIfaceName, "ingress")
	for _, entry := range db.Passwords {
		if entry == nil || entry.IsDeactivated {
			continue
		}
		if entry.MaxDownMbps <= 0 && entry.MaxUpMbps <= 0 {
			continue
		}
		applySpeedLimitForEntryUnlocked(entry)
	}
}

// dtlsDeviceConnectionsLocked считает живые DTLS/TLS-сессии пары пароль+устройство.
// Вызывать под dbMutex (credentialConnections имеет собственный мьютекс).
func dtlsDeviceConnectionsLocked(password, deviceID string) int {
	ownerID := wrapKeyID(password)
	credentialConnections.Lock()
	defer credentialConnections.Unlock()
	count := 0
	for _, activeDeviceID := range credentialConnections.items[ownerID] {
		if deviceID == "" || activeDeviceID == deviceID {
			count++
		}
	}
	return count
}

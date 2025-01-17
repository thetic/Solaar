# -*- python-mode -*-
# -*- coding: UTF-8 -*-

## Copyright (C) 2012-2013  Daniel Pavel
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License along
## with this program; if not, write to the Free Software Foundation, Inc.,
## 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

from __future__ import absolute_import, division, print_function, unicode_literals

from time import time as _timestamp

from logging import getLogger, DEBUG as _DEBUG
_log = getLogger(__name__)
del getLogger


from .i18n import _, ngettext
from .common import NamedInts as _NamedInts, NamedInt as _NamedInt
from . import hidpp10 as _hidpp10
from . import hidpp20 as _hidpp20

_R = _hidpp10.REGISTERS

#
#
#

ALERT = _NamedInts(NONE=0x00, NOTIFICATION=0x01, SHOW_WINDOW=0x02, ATTENTION=0x04, ALL=0xFF)

KEYS = _NamedInts(
				BATTERY_LEVEL=1,
				BATTERY_CHARGING=2,
				BATTERY_STATUS=3,
				LIGHT_LEVEL=4,
				LINK_ENCRYPTED=5,
				NOTIFICATION_FLAGS=6,
				ERROR=7,
			)

# If the battery charge is under this percentage, trigger an attention event
# (blink systray icon/notification/whatever).
_BATTERY_ATTENTION_LEVEL = 5

# If no updates have been receiver from the device for a while, ping the device
# and update it status accordinly.
# _STATUS_TIMEOUT = 5 * 60  # seconds
_LONG_SLEEP = 15 * 60  # seconds

#
#
#

def attach_to(device, changed_callback):
	assert device
	assert changed_callback

	if not hasattr(device, 'status') or device.status is None:
		if device.kind is None:
			device.status = ReceiverStatus(device, changed_callback)
		else:
			device.status = DeviceStatus(device, changed_callback)

#
#
#

class ReceiverStatus(dict):
	"""The 'runtime' status of a receiver, mostly about the pairing process --
	is the pairing lock open or closed, any pairing errors, etc.
	"""
	def __init__(self, receiver, changed_callback):
		assert receiver
		self._receiver = receiver

		assert changed_callback
		self._changed_callback = changed_callback

		# self.updated = 0

		self.lock_open = False
		self.new_device = None

		self[KEYS.ERROR] = None

	def __str__(self):
		count = len(self._receiver)
		return (_("No paired devices.") if count == 0 else
			ngettext("%(count)s paired device.", "%(count)s paired devices.", count) % { 'count': count })
	__unicode__ = __str__

	def changed(self, alert=ALERT.NOTIFICATION, reason=None):
		# self.updated = _timestamp()
		self._changed_callback(self._receiver, alert=alert, reason=reason)

	# def poll(self, timestamp):
	# 	r = self._receiver
	# 	assert r
	#
	# 	if _log.isEnabledFor(_DEBUG):
	# 		_log.debug("polling status of %s", r)
	#
	# 	# make sure to read some stuff that may be read later by the UI
	# 	r.serial, r.firmware, None
	#
	# 	# get an update of the notification flags
	# 	# self[KEYS.NOTIFICATION_FLAGS] = _hidpp10.get_notification_flags(r)

#
#
#

class DeviceStatus(dict):
	"""Holds the 'runtime' status of a peripheral -- things like
	active/inactive, battery charge, lux, etc. It updates them mostly by
	processing incoming notification events from the device itself.
	"""
	def __init__(self, device, changed_callback):
		assert device
		self._device = device

		assert changed_callback
		self._changed_callback = changed_callback

		# is the device active?
		self._active = None

		# timestamp of when this status object was last updated
		self.updated = 0

	def to_string(self):
		def _items():
			comma = False

			battery_level = self.get(KEYS.BATTERY_LEVEL)
			if battery_level is not None:
				if isinstance(battery_level, _NamedInt):
					yield _("Battery: %(level)s") % { 'level': _(str(battery_level)) }
				else:
					yield _("Battery: %(percent)d%%") % { 'percent': battery_level }

				battery_status = self.get(KEYS.BATTERY_STATUS)
				if battery_status is not None:
					yield ' (%s)' % _(str(battery_status))

				comma = True

			light_level = self.get(KEYS.LIGHT_LEVEL)
			if light_level is not None:
				if comma: yield ', '
				yield _("Lighting: %(level)s lux") % { 'level': light_level }

		return ''.join(i for i in _items())

	def __repr__(self):
		return '{' +  ', '.join('\'%s\': %r' % (k, v) for k, v in self.items()) + '}'

	def __bool__(self):
		return bool(self._active)
	__nonzero__ = __bool__

	def set_battery_info(self, level, status, timestamp=None):
		if _log.isEnabledFor(_DEBUG):
			_log.debug("%s: battery %s, %s", self._device, level, status)

		if level is None:
			# Some notifications may come with no battery level info, just
			# charging state info, so assume the level is unchanged.
			level = self.get(KEYS.BATTERY_LEVEL)
		else:
			assert isinstance(level, int)

		# TODO: this is also executed when pressing Fn+F7 on K800.
		old_level, self[KEYS.BATTERY_LEVEL] = self.get(KEYS.BATTERY_LEVEL), level
		old_status, self[KEYS.BATTERY_STATUS] = self.get(KEYS.BATTERY_STATUS), status

		charging = status in (_hidpp20.BATTERY_STATUS.recharging, _hidpp20.BATTERY_STATUS.slow_recharge)
		old_charging, self[KEYS.BATTERY_CHARGING] = self.get(KEYS.BATTERY_CHARGING), charging

		changed = old_level != level or old_status != status or old_charging != charging
		alert, reason = ALERT.NONE, None

		if _hidpp20.BATTERY_OK(status) and level > _BATTERY_ATTENTION_LEVEL:
			self[KEYS.ERROR] = None
		else:
			_log.warn("%s: battery %d%%, ALERT %s", self._device, level, status)
			if self.get(KEYS.ERROR) != status:
				self[KEYS.ERROR] = status
				# only show the notification once
				alert = ALERT.NOTIFICATION | ALERT.ATTENTION
			if isinstance(level, _NamedInt):
				reason = _("Battery: %(level)s (%(status)s)") % { 'level': _(level), 'status': _(status) }
			else:
				reason = _("Battery: %(percent)d%% (%(status)s)") % { 'percent': level, 'status': status.name }

		if changed or reason:
			# update the leds on the device, if any
			_hidpp10.set_3leds(self._device, level, charging=charging, warning=bool(alert))
			self.changed(active=True, alert=alert, reason=reason, timestamp=timestamp)

	def read_battery(self, timestamp=None):
		if self._active:
			d = self._device
			assert d

			if d.protocol < 2.0:
				battery = _hidpp10.get_battery(d)
			else:
				battery = _hidpp20.get_battery(d)

			# Really unnecessary, if the device has SOLAR_DASHBOARD it should be
			# broadcasting it's battery status anyway, it will just take a little while.
			# However, when the device has just been detected, it will not show
			# any battery status for a while (broadcasts happen every 90 seconds).
			if battery is None and _hidpp20.FEATURE.SOLAR_DASHBOARD in d.features:
				d.feature_request(_hidpp20.FEATURE.SOLAR_DASHBOARD, 0x00, 1, 1)
				return

			if battery is not None:
				level, status = battery
				self.set_battery_info(level, status)
			elif KEYS.BATTERY_STATUS in self:
				self[KEYS.BATTERY_STATUS] = None
				self[KEYS.BATTERY_CHARGING] = None
				self.changed()

	def changed(self, active=None, alert=ALERT.NONE, reason=None, timestamp=None):
		assert self._changed_callback
		d = self._device
		# assert d  # may be invalid when processing the 'unpaired' notification
		timestamp = timestamp or _timestamp()

		if active is not None:
			d.online = active
			was_active, self._active = self._active, active
			if active:
				if not was_active:
					# Make sure to set notification flags on the device, they
					# get cleared when the device is turned off (but not when the device
					# goes idle, and we can't tell the difference right now).
					if d.protocol < 2.0:
						self[KEYS.NOTIFICATION_FLAGS] = d.enable_notifications()

					# If we've been inactive for a long time, forget anything
					# about the battery.
					if self.updated > 0 and timestamp - self.updated > _LONG_SLEEP:
						self[KEYS.BATTERY_LEVEL] = None
						self[KEYS.BATTERY_STATUS] = None
						self[KEYS.BATTERY_CHARGING] = None

					# Devices lose configuration when they are turned off,
					# make sure they're up-to-date.
					# _log.debug("%s settings %s", d, d.settings)
					for s in d.settings:
						s.apply()

					if self.get(KEYS.BATTERY_LEVEL) is None:
						self.read_battery(timestamp)
			else:
				if was_active:
					battery = self.get(KEYS.BATTERY_LEVEL)
					self.clear()
					# If we had a known battery level before, assume it's not going
					# to change much while the device is offline.
					if battery is not None:
						self[KEYS.BATTERY_LEVEL] = battery

		if self.updated == 0 and active == True:
			# if the device is active on the very first status notification,
			# (meaning just when the program started or a new receiver was just
			# detected), pop-up a notification about it
			alert |= ALERT.NOTIFICATION
		self.updated = timestamp

		# if _log.isEnabledFor(_DEBUG):
		# 	_log.debug("device %d changed: active=%s %s", d.number, self._active, dict(self))
		self._changed_callback(d, alert, reason)

	# def poll(self, timestamp):
	# 	d = self._device
	# 	if not d:
	# 		_log.error("polling status of invalid device")
	# 		return
	#
	# 	if self._active:
	# 		if _log.isEnabledFor(_DEBUG):
	# 			_log.debug("polling status of %s", d)
	#
	# 		# read these from the device, the UI may need them later
	# 		d.protocol, d.serial, d.firmware, d.kind, d.name, d.settings, None
	#
	# 		# make sure we know all the features of the device
	# 		# if d.features:
	# 		# 	d.features[:]
	#
	# 		# devices may go out-of-range while still active, or the computer
	# 		# may go to sleep and wake up without the devices available
	# 		if timestamp - self.updated > _STATUS_TIMEOUT:
	# 			if d.ping():
	# 				timestamp = self.updated = _timestamp()
	# 			else:
	# 				self.changed(active=False, reason='out of range')
	#
	# 		# if still active, make sure we know the battery level
	# 		if KEYS.BATTERY_LEVEL not in self:
	# 			self.read_battery(timestamp)
	#
	# 	elif timestamp - self.updated > _STATUS_TIMEOUT:
	# 		if d.ping():
	# 			self.changed(active=True)
	# 		else:
	# 			self.updated = _timestamp()

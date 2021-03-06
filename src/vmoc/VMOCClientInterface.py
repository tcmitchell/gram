#!/usr/bin/python

#----------------------------------------------------------------------
# Copyright (c) 2013-2016 Raytheon BBN Technologies
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and/or hardware specification (the "Work") to
# deal in the Work without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Work, and to permit persons to whom the Work
# is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Work.
#
# THE WORK IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
# OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE WORK OR THE USE OR OTHER DEALINGS
# IN THE WORK.
#----------------------------------------------------------------------

# Thread from a VMOC client (e.g. GRAM) to queue up requests
# For the VMOC managnement interface and send them
# When VMOC is available
# When VMOC goes down and comes back up, re-send all current requests

import json
import logging
import pdb
import socket
import threading
import time
import gram.am.gram.config as config
from VMOCConfig import VMOCSliceConfiguration, VMOCVLANConfiguration

class VMOCClientInterface(threading.Thread):

    # Lock on the queue of pending requests (class variable)
    _lock = threading.RLock() 

    # Queue of messages to send to VMOC Mgt I/F (class variable)
    # Stored as {'slice_id':slice_id,'msg':msg}
    _pending_queue = [] 

    # Per-slice current configuration (class variable)
    _configs_by_slice = {} 

    # Singleton instance of interface
    _instance = None

    # How often to try to reconnect to VMOC?
    _ping_interval = 5

    # What is the PID of the VMOC process we've been talking to?
    _vmoc_pid = None

    # Has the VMOC restarted since last check (the PID is different)
    _vmoc_restarted = False

    def __init__(self):

        threading.Thread.__init__(self)
        self._running = False
        self._vmoc_is_up = False
        self._vmoc_host = 'localhost'

        # First time we run, tell the client to 
        # erase any previous slice info
        VMOCClientInterface.clear()


    # Thread loop
    # Maintain a socket connection
    # Every second, try to ping. 
    # If you fail, try to reconnect
    # And send every message out afresh when you reconnect
    # If connection is open, send all pending messages
    def run(self):

      self._running = True

      while self._running:

          latest_vmoc_pid = None

#          config.logger.info("VMOC_IS_UP : "+ str(self._vmoc_is_up));

          # Is VMOC down? If so, flush queue and fill with all configs
          try:
              sock = self.connectToVMOC()
#              config.logger.info("SOCK = " + str(sock));
              if sock:
                  sock.send('ping')
                  latest_vmoc_pid = sock.recv(1024)
#                  config.logger.info("VMOC_PID /" + latest_vmoc_pid + "/ " + str(len(latest_vmoc_pid)))
                  if latest_vmoc_pid != self._vmoc_pid:
                      config.logger.info("VMOC_PID changed from %s to %s" % \
                                             (self._vmoc_pid, latest_vmoc_pid))
                      self._vmoc_restarted = True
                      self._vmoc_pid = latest_vmoc_pid
                  sock.close()
#                  config.logger.info("After close");
                  self._vmoc_is_up =  True
              else:
                  if self._vmoc_is_up:
                      config.logger.info("VMOC went down ")
                      self._vmoc_is_up = False

          except Exception as e:
              config.logger.info("Exception (1) : " + str(e))
              if self._vmoc_is_up:
                  config.logger.info("VMOC went down " + str(e))
                  self._vmoc_is_up  = False

#          config.logger.info("UP = " + str(self._vmoc_is_up))
#          config.logger.info("RESTARTED = " + str(self._vmoc_restarted))

                  
          # Connection is down or recently reset. 
          # Put all configs into queue
          if not self._vmoc_is_up or self._vmoc_restarted:
              with VMOCClientInterface._lock:
                  slice_configs = \
                      VMOCClientInterface._configs_by_slice.values()
                  VMOCClientInterface._pending_queue = []
#                  config.logger.info("Disconnected from VMOC: " + 
#                                     "restoring slice configs")
                  for slice_config in slice_configs:
                      VMOCClientInterface.register(slice_config)
                  self._vmoc_restarted = False



  # If connected, try to send all messages
  # If we fail sending any message, connection is closed
          with VMOCClientInterface._lock:
              while len(VMOCClientInterface._pending_queue) > 0 and self._vmoc_is_up:
                  entry = VMOCClientInterface._pending_queue[0]
                  msg = entry['msg']
                  try:
                      sock = self.connectToVMOC()
#                      print " Trying to send " + str(entry)
                      if sock:
                          sock.send(msg)
                          sock.close()
                          VMOCClientInterface._pending_queue = \
                              VMOCClientInterface._pending_queue[1:]
                          config.logger.info("Sent to VMOC: " + msg)
                      else:
                          self._vmoc_is_up = False
                  except Exception as e:
#                      print "Exception (2) "  + str(e)
                      self._vmoc_is_up = False

          # Wake up every N seconds
          time.sleep(VMOCClientInterface._ping_interval)

    # Establish a connection to VMOC Management interface
    def connectToVMOC(self):
        sock = None
        addr = (self._vmoc_host, config.vmoc_interface_port)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(addr)
        except Exception as e:
            sock = None
        return sock

    # Start thread for VMOC Client I/F
    @staticmethod
    def startup():
        VMOCClientInterface._instance = VMOCClientInterface()
        VMOCClientInterface._instance.start()

    # Shutdown thread for VMOC Client I/F
    @staticmethod
    def shutdown():
        VMOCClientInterface._instance._running = False
        VMOCClientInterface._instance.join()

    @staticmethod
    def create_queue_entry(slice_id, slice_config, register):
        if not slice_config:
#            print "SLICE_ID = " + str(slice_id)
#            print "CONFIGS_BY_SLICE = " + str(VMOCClientInterface._instance._configs_by_slice)
            slice_config = VMOCClientInterface._instance._configs_by_slice[slice_id];
        if slice_config: slice_id = slice_config.getSliceID()
        if register:
            msg = 'register %s' % json.dumps(slice_config.__attr__())
        else:
#            msg = 'unregister %s' % slice_id
            msg = 'unregister %s' % json.dumps(slice_config.__attr__())
        entry = {'slice_id': slice_id, 'msg':msg}
        return entry

    @staticmethod
    def register(slice_config):
        with VMOCClientInterface._lock:
            slice_id = slice_config.getSliceID()
            queue_entry = \
                VMOCClientInterface.create_queue_entry(slice_id, \
                                                           slice_config, True)
            VMOCClientInterface._pending_queue.append(queue_entry)
            VMOCClientInterface._configs_by_slice[slice_id] = slice_config
#        VMOCClientInterface.dumpQueue()

    @staticmethod
    def unregister(slice_config):
        with VMOCClientInterface._lock:
            slice_id = slice_config.getSliceID()
            VMOCClientInterface._pending_queue = \
                [msg for msg in VMOCClientInterface._pending_queue if msg['slice_id'] != slice_id]
            queue_entry = \
                VMOCClientInterface.create_queue_entry(slice_id, slice_config, False)
            VMOCClientInterface._pending_queue.append(queue_entry)
            if VMOCClientInterface._configs_by_slice.has_key(slice_id):
                del VMOCClientInterface._configs_by_slice[slice_id]
#        VMOCClientInterface.dumpQueue()

    @staticmethod
    def clear():
        with VMOCClientInterface._lock:
            clear_entry = {'slice_id': None, 'msg': "clear"}
            VMOCClientInterface._pending_queue.append(clear_entry)
#        VMOCClientInterface.dumpQueue()

    @staticmethod
    def dumpQueue():
        logger.info("Pending VMOC Request Queue:")
        with VMOCClientInterface._lock:
            for entry in VMOCClientInterface._pending_queue:
                logger.info("   " + str(entry))


if __name__ == "__main__":

    logger = config.logger
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    logger.addHandler(handler)

    VMOCClientInterface.startup()
    vlan_config1 = VMOCVLANConfiguration(vlan_tag=1001, controller_url=None)
    slice_config1 = \
        VMOCSliceConfiguration(slice_id='S1', vlan_configs=[vlan_config1])
    VMOCClientInterface.register(slice_config1)
    vlan_config2 = VMOCVLANConfiguration(vlan_tag=1002, controller_url=None)
    slice_config2 = \
        VMOCSliceConfiguration(slice_id='S2', vlan_configs=[vlan_config2])
    VMOCClientInterface.register(slice_config2)
    VMOCClientInterface.unregister('S2')

#    VMOCClientInterface.shutdown()




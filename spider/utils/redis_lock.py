# -*- coding: utf-8 -*-
"""
Created on 2019/11/5 5:25 PM
---------
@summary:
---------
@author: Boris
@email: boris@bzkj.tech
"""
import time

import redis

import spider.utils.log as log

# redis锁使用的redis连接池
global_redis_lock_connection_pool_cache = {}


class RedisLock(object):
    def __init__(
        self,
        key,
        timeout=300,
        wait_timeout=8 * 3600,
        break_wait=None,
        redis_uri=None,
        connection_pool=None,
        logger=None,
    ):
        """
        redis超时锁
        :param key: 关键字  不同项目区分
        :param timeout: 锁超时时间
        :param wait_timeout:  等待加锁超时时间 默认8小时  防止多线程竞争时可能出现的 某个线程无限等待
                            <=0 则不等待 直接加锁失败
        :param break_wait: 可自定义函数 灵活控制 wait_timeout 时间 当此函数返回True时 不再wait

        用法示例:
        with RedisLock(key="test", timeout=10, wait_timeout=100, redis_uri="") as _lock:
            if _lock.locked:
                # 用来判断是否加上了锁
                # do somethings
        """
        self.redis_index = -1
        if not key:
            raise Exception("lock key is empty")
        if connection_pool:
            self.redis_conn = redis.StrictRedis(connection_pool=connection_pool)
        else:
            self.redis_conn = self.get_redis_conn(redis_uri)

        self.logger = logger or log.get_logger(__file__)

        self.lock_key = "redis_lock:{}".format(key)
        # 锁超时时间
        self.timeout = timeout
        # 等待加锁时间
        self.wait_timeout = wait_timeout
        # wait中断函数
        self.break_wait = break_wait
        if self.break_wait is None:
            self.break_wait = lambda: False
        if not callable(self.break_wait):
            raise TypeError(
                "break_wait must be function or None, but: {}".format(
                    type(self.break_wait)
                )
            )

        self.locked = False

    def __enter__(self):
        if not self.locked:
            self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    def __repr__(self):
        return "<RedisLock: {} index: {}>".format(self.lock_key, self.redis_index)

    def get_redis_conn(self, redis_uri):
        if redis_uri not in global_redis_lock_connection_pool_cache:
            connection_pool = redis.BlockingConnectionPool.from_url(
                redis_uri, max_connections=10, timeout=60
            )
            global_redis_lock_connection_pool_cache[redis_uri] = connection_pool
        return redis.StrictRedis(
            connection_pool=global_redis_lock_connection_pool_cache[redis_uri]
        )

    def acquire(self):
        start = time.time()
        self.logger.debug("准备获取锁{} ...".format(self))
        while 1:
            # 尝试加锁
            if self.redis_conn.setnx(self.lock_key, time.time()):
                self.redis_conn.expire(self.lock_key, self.timeout)
                self.locked = True
                self.logger.debug("加锁成功: {}".format(self))
                break
            else:
                # 修复bug： 当加锁时被干掉 导致没有设置expire成功 锁无限存在
                if self.redis_conn.ttl(self.lock_key) < 0:
                    self.redis_conn.delete(self.lock_key)

            if self.wait_timeout > 0:
                if time.time() - start > self.wait_timeout:
                    break
            else:
                # 不等待
                break
            if self.break_wait():
                self.logger.debug("break_wait 生效 不再等待加锁")
                break
            self.logger.debug("等待加锁: {} wait:{}".format(self, time.time() - start))
            if self.wait_timeout > 10:
                time.sleep(5)
            else:
                time.sleep(1)
        return

    def release(self):
        if self.locked:
            self.redis_conn.delete(self.lock_key)
            self.locked = False
        return

    def prolong_life(self, life_time: int) -> int:
        """
        延长这个锁的超时时间
        :param life_time: 延长时间
        :return:
        """
        expire = self.redis_conn.ttl(self.lock_key)
        if expire < 0:
            return expire
        expire += life_time
        self.redis_conn.expire(self.lock_key, expire)
        return self.redis_conn.ttl(self.lock_key)

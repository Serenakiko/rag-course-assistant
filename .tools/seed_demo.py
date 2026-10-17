# -*- coding: utf-8 -*-
"""生成一批演示数据，把管理端各页面填满。

用法：
    python .tools/seed_demo.py            # 造数据（知识点会向量化，便于问答）
    python .tools/seed_demo.py --no-vectors   # 只写数据库，不调 embedding（快）

可重复运行：已存在的同名用户/知识库/知识点/文档会跳过，不会重复插入。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402

from backend.config import settings  # noqa: E402
from backend.database import SessionLocal, init_db  # noqa: E402
from backend.models import (  # noqa: E402
    Conversation,
    Document,
    KnowledgeBase,
    KnowledgePoint,
    Message,
    RecycleItem,
    UnansweredQuestion,
    User,
)
from backend.security import hash_password  # noqa: E402
from backend.services.chunker import split_text  # noqa: E402

random.seed(20261018)
NOW = datetime.now(timezone.utc)
DEMO_PASSWORD = "123456"

SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章"
GIVEN = ["子涵", "雨桐", "浩然", "思远", "欣怡", "嘉懿", "宇轩", "诗涵", "明哲", "雨萱",
         "俊杰", "梓萱", "承泽", "一诺", "书瑶", "天佑", "若汐", "子墨", "语辰", "思睿",
         "静怡", "博文", "安琪", "修远", "清瑶", "知远", "亦航", "欣然", "景铄", "念之",
         "沐宸", "芷若", "望舒", "锦程", "玉洁", "修文", "霈泽", "南乔", "辰逸", "若溪",
         "新宇", "梓琪", "明远", "思齐", "书言"]

# ---------------------------------------------------------------- 课程与知识点
COURSES: list[tuple[str, str, list[tuple[str, str, str]]]] = [
    ("数据结构", "线性表、树、图与排序查找：这门课期末最爱考什么", [
        ("栈与队列", "栈后进先出、只在栈顶操作，队列先进先出、队尾进队头出。栈常见于函数调用与表达式求值，队列常见于任务排队与广度优先搜索。", "栈,队列,线性表"),
        ("链表与数组", "数组支持随机访问但插入删除要搬移元素；链表插入删除只需改指针，但不支持随机访问且要多存指针。实际选择看访问与修改的比例。", "链表,数组"),
        ("二叉树的遍历", "二叉树有先序、中序、后序和层序四种遍历。先序适合复制树结构，中序在二叉搜索树上得到有序序列，层序用队列实现。", "二叉树,遍历"),
        ("二叉搜索树", "二叉搜索树满足左子树小于根、右子树大于根，查找平均 O(log n)。但如果插入序列有序就会退化成链表，需要平衡树来兜底。", "BST,查找"),
        ("红黑树", "红黑树用五个性质约束颜色，保证最长路径不超过最短路径的两倍，从而把树高控制在 O(log n)。插入和删除通过旋转与变色恢复性质。", "红黑树,平衡"),
        ("哈希表与冲突处理", "哈希表用哈希函数把键映射到桶，理想情况 O(1) 查找。冲突处理有链地址法和开放定址法，装填因子过高会导致性能急剧下降。", "哈希,冲突"),
        ("图的存储与遍历", "图可用邻接矩阵或邻接表存储，前者适合稠密图、能 O(1) 判断边，后者省空间。BFS 用队列、DFS 用栈或递归。", "图,邻接表"),
        ("最小生成树", "Prim 从点出发每次加入最近顶点，Kruskal 从边出发按权排序并查集合并。两者都基于贪心，结果总权值相同。", "Prim,Kruskal,生成树"),
        ("堆与优先队列", "堆是完全二叉树且满足堆序，插入和删除都是 O(log n)，取最值 O(1)。建堆可以自底向上下滤，复杂度 O(n)。", "堆,优先队列"),
        ("排序算法稳定性", "稳定排序保证相等元素相对次序不变，如插入排序、归并排序；选择排序、快速排序和堆排序不稳定。需要稳定时优先归并。", "排序,稳定性"),
        ("递归与分治", "递归要有终止条件和规模递减，否则栈溢出。分治把问题拆成独立子问题，归并排序与快速排序都是典型。", "递归,分治"),
        ("复杂度分析", "时间复杂度看循环层数和递归式，空间复杂度看额外开辟的数组或递归深度。分析时要看最坏和平均，而不是只看最好情况。", "复杂度,空间"),
    ]),
    ("操作系统", "进程、内存、文件与并发：看懂操作系统在替你做什么", [
        ("进程与线程", "进程是资源分配的基本单位，拥有独立地址空间；线程是调度的基本单位，共享进程资源。线程切换开销更小，但一个线程出错可能拖垮整个进程。", "进程,线程"),
        ("进程状态转换", "进程有就绪、运行、阻塞三种基本状态。时间片用完从运行回就绪，等待资源从运行转阻塞，资源就绪从阻塞回就绪。", "进程,状态机"),
        ("进程调度算法", "常见调度有先来先服务、短作业优先、时间片轮转和优先级调度。多级反馈队列按优先级分层，兼顾响应时间与吞吐。", "调度,时间片"),
        ("死锁与银行家算法", "死锁需要互斥、占有并等待、不可抢占和循环等待四个条件同时成立。银行家算法在分配前判断系统是否仍处于安全状态，否则暂缓分配。", "死锁,银行家"),
        ("分页与分段", "分页把地址空间等分成页，解决外部碎片但产生内部碎片；分段按逻辑单位划分，便于共享和保护但产生外部碎片。段页式结合两者。", "分页,分段"),
        ("虚拟内存与页面置换", "虚拟内存让程序不必全部装入内存。缺页时按页面置换算法换出，OPT 理论最优、LRU 接近最优、FIFO 可能出现 Belady 异常。", "虚拟内存,LRU"),
        ("文件系统结构", "文件系统用 inode 或 FAT 记录文件的元数据和数据块位置，目录是文件名到 inode 的映射。空闲空间用位图或空闲链表管理。", "文件系统,inode"),
        ("磁盘调度算法", "磁盘调度有先来先服务、最短寻道优先、SCAN 和 CSCAN。SCAN 像电梯一样来回扫，避免饥饿但边缘磁道等待较久。", "磁盘,寻道"),
        ("中断与系统调用", "系统调用是用户态请求内核服务的入口，通过陷入指令切换到内核态。中断由硬件触发，异常由指令执行错误触发。", "中断,系统调用"),
        ("信号量与 PV 操作", "信号量用于进程同步与互斥，P 操作申请资源、V 操作释放资源。互斥信号量初值为 1，同步信号量按可用资源数设置。", "信号量,PV"),
        ("临界区与互斥", "临界区是访问共享资源的代码段，同一时刻只能有一个进程进入。软件方案有 Peterson 算法，硬件方案有 TestAndSet 和关中断。", "临界区,互斥"),
        ("用户态与内核态", "CPU 分用户态和内核态，特权指令只能在内核态执行。用户程序需要内核服务时必须通过系统调用，切换有开销。", "内核态,特权指令"),
    ]),
    ("计算机网络", "从分层到握手：把网络知识串成一条线", [
        ("OSI 七层模型", "OSI 分物理层、数据链路层、网络层、传输层、会话层、表示层和应用层。实际使用的 TCP/IP 四层模型把上三层合并为应用层。", "OSI,分层"),
        ("TCP 三次握手", "客户端发 SYN，服务器回 SYN+ACK，客户端再回 ACK。三次是为了让双方都确认对方的收发能力都正常，两次无法确认客户端的接收能力。", "TCP,握手"),
        ("TCP 四次挥手", "主动关闭方发 FIN，对方 ACK 后可能还有数据要发，发完再发 FIN，最后 ACK 确认。TIME_WAIT 等待 2MSL 是为了保证最后的 ACK 能到达。", "TCP,挥手,TIME_WAIT"),
        ("TCP 可靠传输", "TCP 用序号、确认和重传保证可靠，配合滑动窗口做流量控制。超时重传时间根据往返时延动态估算。", "TCP,滑动窗口"),
        ("TCP 拥塞控制", "慢启动指数增长阈值后转拥塞避免线性增长，出现超时则拥塞窗口减半并重新慢启动。快速重传与快速恢复减少等待。", "拥塞控制,慢启动"),
        ("UDP 与 TCP 的区别", "UDP 无连接、不保证可靠、头部只有 8 字节，适合实时音视频和 DNS；TCP 面向连接、可靠、有序，适合文件传输。", "UDP,对比"),
        ("IP 地址与子网掩码", "IPv4 地址 32 位，子网掩码决定网络位与主机位。CIDR 用 /n 表示前缀长度，子网划分减少广播域。", "IP,子网掩码"),
        ("路由选择", "距离向量路由用跳数迭代交换，可能出现计数到无穷；链路状态路由收集全网拓扑跑 Dijkstra。实际网络用 BGP 在自治系统之间选路。", "路由,RIP,OSPF"),
        ("DNS 解析过程", "DNS 递归查询先问本地域名服务器，再逐级向根、顶级和权威服务器查询。结果按 TTL 缓存，减少重复查询。", "DNS,解析"),
        ("HTTP 与状态码", "HTTP 是无状态的应用层协议，用方法表达语义。状态码 2xx 成功、3xx 重定向、4xx 客户端错误、5xx 服务端错误。", "HTTP,状态码"),
        ("HTTPS 加密过程", "HTTPS 在 TCP 之上加 TLS：先用非对称加密协商出会话密钥，再用对称加密传输数据，并用证书链验证服务器身份。", "HTTPS,TLS,证书"),
        ("ARP 与 MAC 地址", "ARP 把 IP 地址解析成 MAC 地址，在局域网内广播询问、单播应答，结果缓存一段时间。跨网段时源 MAC 会逐跳改变。", "ARP,MAC"),
    ]),
    ("数据库原理", "关系模型、事务与索引：把数据管对管快", [
        ("关系模型与键", "关系模型用二维表表示实体与联系，主键唯一标识元组，外键维护参照完整性。候选键是最小的唯一标识属性组。", "关系模型,主键"),
        ("SQL 查询执行顺序", "SQL 书写顺序是 select、from、where、group by、having、order by，但逻辑执行顺序是 from → where → group by → having → select → order by。", "SQL,执行顺序"),
        ("事务的 ACID", "事务有原子性、一致性、隔离性和持久性。原子性靠回滚日志，持久性靠重做日志，隔离性靠锁或 MVCC。", "事务,ACID"),
        ("隔离级别与并发问题", "读未提交会脏读，读已提交会不可重复读，可重复读会幻读，串行化最安全但并发最低。MySQL 默认可重复读并用间隙锁抑制幻读。", "隔离级别,幻读"),
        ("索引与 B+ 树", "B+ 树把数据都存在叶子节点并用链表相连，非叶子只存键，因此层数低、范围查询快，是数据库索引的默认结构。", "索引,B+树"),
        ("范式与函数依赖", "1NF 要求属性不可再分，2NF 消除非主属性对候选键的部分依赖，3NF 再消除传递依赖，BCNF 要求每个决定因素都包含候选键。", "范式,函数依赖"),
        ("视图与触发器", "视图是保存下来的查询，可以简化复杂查询并做权限控制；触发器在增删改前后自动执行，容易造成隐式副作用，慎用。", "视图,触发器"),
        ("锁机制", "数据库锁分共享锁和排他锁，按粒度有行锁、页锁和表锁。行锁并发高但开销大，表锁简单但容易阻塞。", "锁,并发"),
        ("查询优化", "查询优化看执行计划，关注是否走索引、有没有回表和文件排序。常见手段有加合适索引、避免函数包裹字段、减少 select *。", "优化,执行计划"),
        ("主从复制", "主库写 binlog，从库拉取并重放实现复制。异步复制延迟小但可能丢数据，半同步在主库等待至少一个从库确认。", "复制,binlog"),
        ("关系库与 NoSQL", "关系库强一致、支持事务和复杂查询；NoSQL 在扩展性和吞吐上更强，但一致性模型弱。选型看数据结构和一致性要求。", "NoSQL,选型"),
        ("SQL 注入与防护", "SQL 注入是把用户输入拼进 SQL 导致的越权执行。防护的首选是参数化查询，其次是最小权限和输入校验。", "注入,参数化"),
    ]),
    ("计算机组成原理", "从冯·诺依曼结构到流水线，讲清 CPU、存储与 IO 的组成原理", [
        ("冯·诺依曼结构", "冯·诺依曼结构由运算器、控制器、存储器、输入设备和输出设备五部分组成，特点是存储程序并按地址顺序执行。数据和指令存放在同一存储器中，以二进制表示。", "体系结构,存储程序"),
        ("存储层次结构", "存储层次从上到下依次为寄存器、Cache、主存、辅存，越靠上速度越快、容量越小、单位价格越高。层次结构利用程序访问的局部性，让整体接近最快的层次。", "Cache,局部性"),
        ("Cache 地址映射", "Cache 与主存之间有三种映射方式：直接映射、全相联映射和组相联映射。直接映射实现简单但冲突多，全相联灵活但比较器多，组相联是两者的折中。", "Cache,映射"),
        ("指令格式与寻址方式", "一条指令通常由操作码和地址码组成。常见寻址方式有立即寻址、直接寻址、间接寻址、寄存器寻址、变址寻址和相对寻址，寻址方式决定了操作数从哪里取。", "指令,寻址"),
        ("指令流水线", "流水线把指令执行拆成取指、译码、执行、访存、写回等阶段并行处理。流水线会面临结构相关、数据相关和控制相关三类冲突，需要用停顿、转发或分支预测解决。", "流水线,冲突"),
        ("中断与异常", "中断由外部设备请求产生，异常由指令执行过程中的事件产生。CPU 响应中断时要保存断点和现场，转入中断服务程序，处理完再恢复现场返回。", "中断,异常"),
        ("浮点数表示", "浮点数由符号位、阶码和尾数组成，IEEE 754 单精度为 1+8+23 位，双精度为 1+11+52 位。规格化后尾数隐含最高位 1，阶码采用移码表示。", "浮点数,IEEE754"),
        ("总线与 IO 方式", "IO 方式包括程序查询、中断、DMA 和通道方式。DMA 由控制器直接在内存与外设间传送数据，不占用 CPU，适合大批量高速传输。", "总线,DMA"),
        ("RISC 与 CISC", "CISC 指令多且复杂，单条指令功能强；RISC 指令少而规整，强调流水线和寄存器操作。RISC 通常采用定长指令和 load/store 结构。", "RISC,CISC"),
        ("虚拟存储器", "虚拟存储器把主存和辅存统一编址，用页表完成逻辑地址到物理地址的转换。TLB 缓存页表项，缺页时由操作系统调入页面。", "虚拟存储,页表"),
        ("定点数运算", "定点数用补码表示可以把减法统一为加法。补码运算时符号位参与运算，溢出通过双符号位或进位判断。", "补码,溢出"),
        ("指令周期", "指令周期指取出一条指令并执行完毕所需的时间，可划分为取指周期、间址周期、执行周期和中断周期，各周期由不同的微操作序列组成。", "指令周期,微操作"),
    ]),
    ("算法设计与分析", "分治、动态规划、贪心、回溯等经典算法范式与复杂度分析", [
        ("时间复杂度分析", "时间复杂度描述算法运行时间随输入规模的增长趋势，用大 O 记号表示上界。常见量级从快到慢为 O(1)、O(log n)、O(n)、O(n log n)、O(n²)、O(2ⁿ)。", "复杂度,大O"),
        ("分治法", "分治把问题分解为若干规模更小的同类子问题，递归求解后合并结果。归并排序和快速排序都是典型分治，关键是子问题独立且合并代价可控。", "分治,递归"),
        ("动态规划", "动态规划要求问题具有最优子结构和重叠子问题，通过保存子问题结果避免重复计算。常见步骤是定义状态、写出转移方程、确定边界和计算顺序。", "动态规划,状态转移"),
        ("贪心算法", "贪心在每一步选择当前最优，不回溯。只有当问题具有贪心选择性质和最优子结构时贪心才正确，例如活动安排和霍夫曼编码。", "贪心,最优子结构"),
        ("回溯法", "回溯法在解空间树上深度优先搜索，遇到不满足约束的分支就剪枝回退。N 皇后和子集和问题都是典型应用。", "回溯,剪枝"),
        ("最短路径算法", "Dijkstra 适用于非负权单源最短路，复杂度 O(n²) 或配合堆优化；Floyd 求任意两点最短路，三重循环 O(n³)，可以处理负权但不能有负环。", "Dijkstra,Floyd"),
        ("最小生成树", "Prim 从一个顶点出发不断加入最近顶点，适合稠密图；Kruskal 按边权排序用并查集合并，适合稀疏图。两者都基于贪心且结果总权值相同。", "Prim,Kruskal"),
        ("排序算法比较", "快速排序平均 O(n log n) 但不稳定，归并排序稳定且最坏也是 O(n log n) 但需要额外空间，堆排序原地但不稳定，插入排序适合基本有序的小数组。", "排序,稳定性"),
        ("主定理", "主定理用于求解 T(n)=aT(n/b)+f(n) 型递归式，比较 f(n) 与 n^(log_b a) 的量级即可得到复杂度。它是分析分治算法的主要工具。", "主定理,递归式"),
        ("NP 完全问题", "P 类问题可在多项式时间求解，NP 类问题可在多项式时间验证。NP 完全问题是 NP 中最难的一类，只要有一个被多项式求解则 P=NP。", "NP,P与NP"),
        ("字符串匹配 KMP", "KMP 通过预处理模式串的 next 数组，在失配时让模式串右移而不是回退主串指针，从而把匹配复杂度降到 O(n+m)。", "KMP,字符串"),
        ("并查集", "并查集支持合并集合与查询所属集合，配合路径压缩和按秩合并后接近常数时间，常用于连通性判断和 Kruskal 算法。", "并查集,连通性"),
    ]),
    ("软件工程", "需求、设计、测试与团队协作：把一个项目从想法做到可维护", [
        ("软件生命周期", "软件生命周期包括可行性分析、需求分析、设计、编码、测试、运行维护等阶段。不同模型对阶段的组织方式不同，瀑布强调顺序，迭代强调循环。", "生命周期,阶段"),
        ("需求分析", "需求分析要产出需求规格说明书，区分功能需求、非功能需求和约束。需求要可验证、无歧义，变更要有评审和记录。", "需求,规格说明"),
        ("UML 用例图", "用例图描述系统边界、参与者和用例之间的关系，用于表达系统能做什么。用例之间可以有包含、扩展和泛化关系。", "UML,用例"),
        ("UML 类图", "类图描述类的属性、方法以及类之间的关联、聚合、组合、继承和依赖关系，是面向对象设计的核心图。", "UML,类图"),
        ("单例模式", "单例保证一个类只有一个实例并提供全局访问点，常见实现有饿汉式、懒汉式加双检锁和静态内部类。要注意多线程和反射破坏单例的问题。", "设计模式,单例"),
        ("工厂模式", "工厂模式把对象创建封装起来，调用方只依赖接口不依赖具体类。简单工厂、工厂方法和抽象工厂的抽象层次依次提高。", "设计模式,工厂"),
        ("敏捷开发", "敏捷强调短迭代、可工作软件和快速响应变化，Scrum 用产品待办、冲刺和每日站会组织协作。它并不等于没有文档。", "敏捷,Scrum"),
        ("单元测试", "单元测试针对最小可测单元验证行为，要求快速、独立、可重复。测试用例应覆盖正常路径、边界值和异常输入。", "测试,单元测试"),
        ("耦合与内聚", "高内聚低耦合是模块划分的目标：模块内部职责单一，模块之间依赖尽量少。耦合从低到高有数据耦合、标记耦合、控制耦合和内容耦合。", "耦合,内聚"),
        ("版本控制 Git", "Git 用提交记录快照，分支只是指向提交的指针。常用流程是拉取、修改、暂存、提交、推送，冲突在合并时手工解决。", "Git,版本控制"),
        ("持续集成", "持续集成要求频繁把代码合入主干并自动构建、自动测试，尽早暴露集成问题。流水线通常包括构建、静态检查、测试和部署。", "CI,流水线"),
        ("软件度量", "常用度量包括代码行数、圈复杂度、缺陷密度和测试覆盖率。度量用于发现风险，不应成为考核工具，否则容易被刷指标。", "度量,复杂度"),
    ]),
    ("编译原理", "从词法分析到目标代码生成，理解程序是怎么被翻译成机器的语言", [
        ("词法分析", "词法分析把字符流切分成单词（token），识别关键字、标识符、常量和界符，通常用有限自动机实现。", "词法,自动机"),
        ("正则表达式与 NFA", "正则表达式可以描述词法规则，先转成 NFA 再确定化为 DFA，最后用 DFA 驱动词法分析器。", "正则,NFA,DFA"),
        ("语法分析 LL(1)", "LL(1) 是自顶向下分析，要求文法无左递归、无公共左因子，通过 FIRST 集和 FOLLOW 集构造预测分析表。", "LL1,FIRST,FOLLOW"),
        ("LR 分析", "LR 分析自底向上，用状态栈和符号栈进行移进归约。SLR、LR(1) 和 LALR 的区别在于向前看符号的精度和状态数量。", "LR,移进归约"),
        ("语法树", "语法树（分析树）反映推导过程，抽象语法树则去掉冗余结点只保留语义结构，是后续语义分析和代码生成的输入。", "语法树,AST"),
        ("语义分析", "语义分析检查类型是否匹配、变量是否声明、是否有非法运算，并填充符号表。类型检查是其中的核心工作。", "语义,类型检查"),
        ("中间代码生成", "中间代码与具体机器无关，常见形式有三地址码、四元式和逆波兰式，便于优化和移植。", "中间代码,三地址码"),
        ("代码优化", "优化分为局部优化、循环优化和全局优化，常见手段有常量折叠、公共子表达式消除、强度削弱和循环不变代码外提。", "优化,循环"),
        ("符号表", "符号表记录标识符的名字、类型、作用域和存储位置，支持插入、查找和作用域嵌套的管理。", "符号表,作用域"),
        ("上下文无关文法", "上下文无关文法由终结符、非终结符、产生式和开始符号组成，用来描述程序的结构。二义性文法会导致分析结果不唯一。", "文法,二义性"),
        ("运行时存储组织", "程序运行时内存分为代码区、静态区、栈区和堆区。函数调用时在栈上分配活动记录，动态对象在堆上分配。", "运行时,活动记录"),
        ("目标代码生成", "目标代码生成把中间代码翻译成汇编或机器码，需要完成寄存器分配和指令选择，尽量减少访存次数。", "目标代码,寄存器"),
    ]),
    ("人工智能导论", "搜索、机器学习与神经网络的基础概念与常见模型", [
        ("状态空间搜索", "搜索问题由状态、动作、转移和代价组成。BFS 按层扩展保证最短路径，DFS 省空间但可能很深，需要防止重复访问。", "搜索,BFS,DFS"),
        ("A* 搜索", "A* 用 f(n)=g(n)+h(n) 评估结点，h 可采纳时能保证最优解。h 越接近真实代价，搜索效率越高。", "A星,启发式"),
        ("机器学习基本概念", "机器学习从数据中学习规律，分为监督学习、无监督学习和强化学习。模型在训练集上拟合，在测试集上评估泛化。", "机器学习,监督学习"),
        ("决策树", "决策树按特征划分样本，用信息增益或基尼指数选择划分属性。树太深会过拟合，需要剪枝。", "决策树,信息增益"),
        ("神经网络基础", "神经元对输入加权求和后经激活函数输出，多层网络通过非线性激活获得表达能力。常见激活函数有 ReLU、Sigmoid 和 Tanh。", "神经网络,激活函数"),
        ("反向传播", "反向传播用链式法则从输出层向前计算梯度，再配合梯度下降更新参数。学习率过大会震荡，过小会收敛慢。", "反向传播,梯度下降"),
        ("卷积神经网络", "CNN 用卷积核提取局部特征，配合池化降采样，具有参数共享和平移不变性，适合处理图像。", "CNN,卷积"),
        ("过拟合与正则化", "过拟合指模型记住训练数据而在新数据上表现差，可用更多数据、简化模型、Dropout、L1/L2 正则和早停缓解。", "过拟合,正则化"),
        ("K-means 聚类", "K-means 迭代把样本分配到最近中心再更新中心，直到收敛。K 需要预先指定，对初始中心敏感，适合球形簇。", "聚类,K-means"),
        ("评估指标", "分类常用准确率、精确率、召回率和 F1，正负样本不平衡时准确率会失真，要看精确率和召回率。", "评估,精确率,召回率"),
        ("大语言模型", "大语言模型基于 Transformer 在海量文本上预训练，通过下一个词预测学习语言规律，再用指令微调对齐人类偏好。", "LLM,Transformer"),
        ("强化学习基础", "强化学习中智能体在环境中采取动作获得奖励，目标是最大化累积回报。核心概念有状态、动作、策略和价值函数。", "强化学习,奖励"),
    ]),
    ("概率论与数理统计", "随机事件、随机变量与统计推断的基本方法", [
        ("随机事件与概率", "随机试验的样本空间包含所有可能结果，事件是样本空间的子集。概率满足非负性、规范性和可列可加性。", "随机事件,概率"),
        ("条件概率与独立性", "条件概率 P(A|B)=P(AB)/P(B) 描述已知 B 发生时 A 的概率。若 P(AB)=P(A)P(B) 则 A、B 独立。", "条件概率,独立"),
        ("全概率公式", "全概率公式把复杂事件按完备事件组分解后求和，是计算总概率的常用方法，常与贝叶斯公式配合使用。", "全概率,完备事件组"),
        ("贝叶斯公式", "贝叶斯公式由条件概率推出后验概率，是垃圾邮件过滤、医学检验等场景的基础。", "贝叶斯,后验"),
        ("随机变量与分布函数", "随机变量把样本点映射为实数，分布函数 F(x)=P(X≤x) 单调不减、右连续，且两端极限为 0 和 1。", "随机变量,分布函数"),
        ("数学期望", "期望是随机变量的加权平均，反映集中位置。期望具有线性性质，E(aX+bY)=aE(X)+bE(Y) 不要求独立。", "期望,线性"),
        ("方差与标准差", "方差衡量随机变量偏离期望的程度，D(X)=E(X²)-[E(X)]²。标准差是方差的算术平方根，量纲与 X 相同。", "方差,标准差"),
        ("常见分布", "二项分布描述 n 次独立重复试验的成功次数，泊松分布描述稀有事件，正态分布由均值和方差决定且可标准化。", "二项分布,正态分布"),
        ("大数定律", "大数定律说明样本均值在样本量增大时依概率收敛到总体期望，是频率估计概率的理论依据。", "大数定律,收敛"),
        ("中心极限定理", "中心极限定理指出独立同分布随机变量之和标准化后近似服从正态分布，与原始分布无关，这是抽样推断的基础。", "中心极限定理,正态"),
        ("参数估计", "点估计给出参数的一个值，常用矩估计和最大似然估计。区间估计给出置信区间，置信度越高区间越宽。", "估计,置信区间"),
        ("假设检验", "假设检验先提出原假设，构造统计量并计算 p 值，p 小于显著性水平时拒绝原假设。要区分第一类错误和第二类错误。", "假设检验,p值"),
    ]),
    ("高等数学", "极限、导数、积分与级数：工科数学分析的主干内容", [
        ("数列与函数极限", "极限描述变量无限接近某值的趋势，判断极限要分清左右极限。等价无穷小替换能大幅简化计算但只在乘除时安全。", "极限,无穷小"),
        ("连续与间断点", "函数在一点连续要求左极限、右极限和函数值三者相等。间断点分为可去、跳跃、无穷和振荡四类。", "连续,间断"),
        ("导数与微分", "导数是函数变化率，几何上表示切线斜率。可导必连续，连续不一定可导（如 y=|x| 在 0 点）。", "导数,切线"),
        ("微分中值定理", "罗尔、拉格朗日和柯西中值定理把函数值与导数值联系起来，是证明不等式和判断单调性的基本工具。", "中值定理,拉格朗日"),
        ("洛必达法则", "洛必达法则用于 0/0 或 ∞/∞ 型未定式，对分子分母分别求导后再取极限，使用前必须验证类型。", "洛必达,未定式"),
        ("不定积分", "不定积分是求导的逆运算，结果要加常数 C。换元法和分部积分是两大基本方法。", "不定积分,换元"),
        ("定积分及其应用", "定积分表示曲边梯形面积，可用来求面积、体积、弧长和物理量。牛顿-莱布尼茨公式把定积分与不定积分联系起来。", "定积分,面积"),
        ("多元函数偏导", "偏导数只对一个变量求导、其余视为常数。全微分存在的必要条件是偏导存在，充分条件是偏导连续。", "偏导,全微分"),
        ("二重积分", "二重积分在直角坐标下化为两次定积分，在极坐标下用 r dr dθ 替换面积元素。选择坐标系要看积分区域和被积函数。", "二重积分,极坐标"),
        ("无穷级数", "级数收敛的必要条件是通项趋于 0，判断收敛常用比值法、根值法和比较法。幂级数在收敛半径内可以逐项求导和积分。", "级数,收敛半径"),
        ("泰勒公式", "泰勒公式用多项式逼近函数，带拉格朗日余项可用于误差估计。常用展开式有 e^x、sin x、ln(1+x)。", "泰勒,展开"),
        ("常微分方程", "一阶线性方程用积分因子法求通解，二阶常系数线性方程先解特征方程再根据重根情况写通解。", "微分方程,特征方程"),
    ]),
    ("离散数学", "逻辑、集合、图论与组合：计算机科学的数学基础", [
        ("命题逻辑", "命题是能判断真假的陈述句，用联结词组成复合命题。真值表可以判断两个命题公式是否等值。", "命题,真值表"),
        ("谓词逻辑", "谓词逻辑引入量词，∀ 表示对所有、∃ 表示存在。量词否定要换量词并否定谓词：¬∀xP(x) 等价于 ∃x¬P(x)。", "谓词,量词"),
        ("集合与运算", "集合运算是交、并、差和补，满足幂等律、交换律、结合律、分配律和德摩根律。可以用文氏图辅助判断恒等式。", "集合,德摩根"),
        ("关系与性质", "二元关系是笛卡尔积的子集，具有自反性、对称性、反对称性和传递性。等价关系对应集合的划分。", "关系,等价关系"),
        ("函数与映射", "函数是满足单值性的关系，分为单射、满射和双射。双射函数存在逆函数。", "函数,双射"),
        ("图的基本概念", "图由顶点和边组成，分为有向图和无向图。顶点的度、路径、连通性、连通分量是基本概念。", "图,度"),
        ("树与生成树", "树是连通无回路的无向图，n 个顶点的树恰有 n-1 条边。连通图的生成树是包含全部顶点的极小连通子图。", "树,生成树"),
        ("欧拉图与哈密顿图", "欧拉图要求存在经过每条边一次的回路，判定条件是连通且所有顶点度数为偶数。哈密顿图要求经过每个顶点一次，判定困难。", "欧拉,哈密顿"),
        ("布尔代数", "布尔代数在 {0,1} 上定义与、或、非，满足分配律和吸收律。逻辑表达式可以化简为最小项之和或卡诺图化简。", "布尔,卡诺图"),
        ("组合计数", "加法法则与乘法法则是最基本的计数原理，排列考虑顺序、组合不考虑顺序，抽屉原理用于证明存在性。", "组合,排列"),
        ("递推关系", "递推关系用前面的项表示后面的项，斐波那契数列是典型例子。求解可用特征方程法或生成函数法。", "递推,特征方程"),
        ("图的矩阵表示", "邻接矩阵描述顶点之间是否有边，邻接矩阵的 k 次幂的元素表示长度为 k 的路径数。", "邻接矩阵,路径"),
    ]),
]

# ---------------------------------------------------------------- 文档与问答素材
DOC_KINDS = ["课件", "课堂讲义", "实验指导", "复习提纲", "作业解析"]

QA_POOL = [
    ("请用通俗的话解释一下 TCP 三次握手的过程", "TCP 建立连接要三次握手：客户端先发 SYN 表示想连接；服务器回 SYN+ACK 表示同意并确认；客户端再回 ACK 确认收到。三步之后双方都确认了彼此的收发能力，连接才建立。两次不够是因为服务器无法确认客户端能收到自己的包。", "计算机网络"),
    ("进程和线程有什么区别？", "进程是资源分配的基本单位，有独立的地址空间；线程是调度的基本单位，同一进程内的线程共享地址空间。线程切换开销小，但一个线程崩溃可能影响整个进程；进程之间隔离性好，通信成本更高。", "操作系统"),
    ("什么是冯·诺依曼结构？", "冯·诺依曼结构把程序和数据都存在同一存储器里，由运算器、控制器、存储器、输入输出设备五部分组成，按地址顺序执行指令。它的核心是存储程序思想，但同一总线传输指令和数据会带来冯·诺依曼瓶颈。", "计算机组成原理"),
    ("栈和队列有什么区别？各自适合什么场景？", "栈是后进先出，只在栈顶操作；队列是先进先出，从队尾进队头出。栈适合函数调用、括号匹配、表达式求值；队列适合任务排队、广度优先搜索和缓冲区。", "数据结构"),
    ("红黑树和普通二叉搜索树相比有什么优势？", "红黑树通过颜色约束和旋转保持近似平衡，树高始终是 O(log n)，因此查找、插入、删除都能保证对数级。普通二叉搜索树在有序数据下会退化成链表，最坏变成 O(n)。", "数据结构"),
    ("索引为什么能加快查询？什么情况下索引会失效？", "索引本质上是排好序的数据结构（如 B+ 树），可以把全表扫描变成定位查找，减少磁盘 IO。但使用函数包裹字段、前导模糊匹配、字段类型不匹配或区分度太低时索引会失效。", "数据库原理"),
    ("什么是死锁？怎样避免？", "死锁是多个进程互相等待对方持有的资源而永远无法推进。产生需要互斥、占有并等待、不可抢占和循环等待四个条件。避免办法包括资源有序分配、一次性申请全部资源，或者用银行家算法做安全检测。", "操作系统"),
    ("虚函数是怎么实现多态的？", "每个含虚函数的类有一张虚函数表，对象里存放指向该表的指针。调用虚函数时通过指针在表里查实际函数地址，从而实现运行时多态。构造函数里调用虚函数不会触发动态绑定。", "程序设计"),
    ("快速排序的最坏情况是什么？怎么避免？", "快速排序在每次划分都极不平衡时退化到 O(n²)，比如对已排序数组固定取第一个元素做基准。用随机基准或三数取中可以大幅降低概率，也可以用栈模拟递归避免深度过大。", "算法设计与分析"),
    ("什么是数据库的第三范式？", "第三范式要求满足第二范式且不存在非主属性对主键的传递依赖。例如把系名、系主任放在学生表里就违反 3NF，因为系主任传递依赖于学号，应拆出系表。", "数据库原理"),
    ("CSS 的盒模型包含哪些部分？", "标准盒模型由 content、padding、border、margin 组成，设置 width 只作用于 content。加上 box-sizing: border-box 后 width 包含 padding 和 border，布局更好算。", "Web 开发"),
    ("什么叫过拟合？怎么缓解？", "过拟合是模型在训练集上表现很好、在新数据上变差，说明它记住了噪声。缓解办法有增加数据、简化模型、正则化、Dropout、早停和交叉验证。", "人工智能导论"),
]

UNANSWERED_POOL = [
    ("红黑树插入后怎么调整才能保持平衡？", "计算机组成原理"),
    ("红黑树插入节点后为什么要旋转", "计算机组成原理"),
    ("红黑树插入的调整过程能详细讲讲吗", "计算机组成原理"),
    ("B+ 树和 B 树的区别在实际项目中怎么选", "数据库原理"),
    ("事务隔离级别里可重复读和幻读是什么关系", "数据库原理"),
    ("MVCC 是怎么实现可重复读的", "数据库原理"),
    ("TCP 拥塞控制有哪些算法，慢启动怎么退出", "计算机网络"),
    ("拥塞控制和流量控制有什么区别", "计算机网络"),
    ("HTTP/2 的多路复用比 HTTP/1.1 强在哪", "计算机网络"),
    ("进程调度算法里多级反馈队列怎么设计", "操作系统"),
    ("页面置换算法 LRU 和 Clock 的实际差别", "操作系统"),
    ("内存泄漏在 Java 里怎么排查", "操作系统"),
    ("快速排序为什么平均是 nlogn 但最坏是 n²", "算法设计与分析"),
    ("动态规划的状态定义有什么技巧", "算法设计与分析"),
    ("NP 完全问题在工程上一般怎么处理", "算法设计与分析"),
    ("编译原理里 LR(1) 和 LALR 合并状态会丢什么信息", "编译原理"),
    ("词法分析为什么不用正则直接匹配完整程序", "编译原理"),
    ("软件工程里需求变更怎么控制", "软件工程"),
    ("单元测试覆盖率要到多少才算够", "软件工程"),
    ("设计模式里的依赖倒置到底怎么落地", "软件工程"),
    ("卷积神经网络里池化层的作用是什么", "人工智能导论"),
    ("Transformer 里的注意力机制为什么比 RNN 强", "人工智能导论"),
    ("交叉验证在样本很少的时候怎么用", "人工智能导论"),
    ("贝叶斯公式在实际题目里怎么找完备事件组", "概率论与数理统计"),
    ("假设检验的 p 值到底怎么理解", "概率论与数理统计"),
    ("中心极限定理和正态分布的关系", "概率论与数理统计"),
    ("洛必达法则什么时候不能用", "高等数学"),
    ("定积分求旋转体体积的公式怎么记", "高等数学"),
    ("二重积分换极坐标时区域怎么划分", "高等数学"),
    ("欧拉图和哈密顿图的判定条件有什么不同", "离散数学"),
    ("邻接矩阵的幂为什么能表示路径条数", "离散数学"),
    ("卡诺图化简的步骤能举个例子吗", "离散数学"),
    ("CPU 流水线里的数据冒险怎么解决", "计算机组成原理"),
    ("Cache 组相联映射的地址怎么划分", "计算机组成原理"),
    ("DMA 和中断方式在什么场景下更合适", "计算机组成原理"),
]

ANSWER_POOL = [
    "这个问题分两步看：先确定概念边界，再看它解决什么矛盾。核心结论是……（演示数据，正式回答由模型生成）",
    "可以从定义出发：它成立的充要条件决定了适用场景，超出这个范围结论就不成立。（演示数据）",
    "教材里通常用反例说明：一旦去掉前提，结论就会崩，所以做题时要先验证条件。（演示数据）",
    "简单说，它和相邻的概念互为补充，一个管正确性，一个管效率。（演示数据）",
]


def _rand_time(days_ago_max: int = 60, weight_recent: bool = False) -> datetime:
    if weight_recent:
        days = random.choice([0, 0, 0, 1, 1, 1, 2, 2, 3, 3, 4, 5, 6])
    else:
        days = random.randint(0, days_ago_max)
    return NOW - timedelta(days=days, hours=random.randint(0, 23), minutes=random.randint(0, 59))


def seed_users(db) -> tuple[User, list[User]]:
    admin = db.scalar(select(User).where(User.username == "23040107")) or db.scalar(
        select(User).where(User.role == "admin").order_by(User.id)
    )
    students: list[User] = []

    # 两个额外的管理员
    for uname, name in (("23040105", "李静怡"), ("23040106", "王博文")):
        if db.scalar(select(User).where(User.username == uname)) is None:
            db.add(
                User(
                    username=uname,
                    password_hash=hash_password(DEMO_PASSWORD),
                    real_name=name,
                    role="admin",
                    created_at=_rand_time(120),
                )
            )
    db.commit()

    existing = {
        u.username
        for u in db.scalars(select(User))
    }
    for i in range(45):
        uname = str(23040201 + i)
        if uname in existing:
            continue
        name = random.choice(SURNAMES) + random.choice(GIVEN)
        students.append(
            User(
                username=uname,
                password_hash=hash_password(DEMO_PASSWORD),
                real_name=name,
                role="student",
                created_at=_rand_time(90),
            )
        )
    db.add_all(students)
    db.commit()

    all_students = list(db.scalars(select(User).where(User.role == "student").order_by(User.id)))
    return admin, all_students


def seed_bases(db, admin: User) -> list[KnowledgeBase]:
    created = []
    for name, desc, _points in COURSES:
        base = db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == name))
        if base is None:
            base = KnowledgeBase(
                name=name,
                description=desc,
                created_by=admin.id,
                created_at=_rand_time(80),
                updated_at=_rand_time(20),
            )
            db.add(base)
            created.append(base)
    db.commit()
    return list(db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.id)))


def seed_points(db, admin: User, with_vectors: bool) -> int:
    from backend.services.vector_store import add_knowledge_point

    total = 0
    for course, _desc, points in COURSES:
        for title, content, tags in points:
            exists = db.scalar(
                select(KnowledgePoint.id).where(
                    KnowledgePoint.course == course, KnowledgePoint.title == title
                )
            )
            if exists is not None:
                continue
            kp = KnowledgePoint(
                title=title,
                content=content,
                course=course,
                tags=tags,
                created_by=admin.id,
                created_at=_rand_time(70),
                updated_at=_rand_time(10),
                is_auto=0,
            )
            db.add(kp)
            db.commit()
            db.refresh(kp)
            total += 1
            if with_vectors:
                try:
                    add_knowledge_point(kp.id, kp.title, kp.content, kp.course, kp.tags)
                except Exception as exc:  # noqa: BLE001
                    print(f"  [warn] 向量化失败 {title}: {exc}")
    return total


def seed_documents(db, admin: User) -> int:
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    made = 0
    for course, _desc, points in COURSES:
        for idx in range(0, len(points), 3):
            group = points[idx : idx + 3]
            kind = DOC_KINDS[(idx // 3) % len(DOC_KINDS)]
            filename = f"{course}-第{idx // 3 + 1}讲 {group[0][0]}-{kind}.txt"
            exists = db.scalar(
                select(Document.id).where(Document.filename == filename, Document.course == course)
            )
            if exists is not None:
                continue
            body = f"{filename}\n\n" + "\n\n".join(
                f"{t}\n{c}" for t, c, _ in group
            )
            path = settings.UPLOAD_DIR / f"demo-{abs(hash(filename)) % 10**10}.txt"
            path.write_text(body, encoding="utf-8")
            chunks = split_text(body)
            db.add(
                Document(
                    filename=filename,
                    file_type="txt",
                    file_size=path.stat().st_size,
                    stored_path=str(path),
                    status="ready",
                    course=course,
                    is_seed=0,
                    chunk_count=len(chunks),
                    error="",
                    uploaded_by=admin.id,
                    created_at=_rand_time(60),
                )
            )
            made += 1
    db.commit()

    # 少数失败的文档，让页面能体现“解析失败 / 重新解析”
    failed = [
        ("操作系统-第9讲 虚拟内存-扫描版.pdf", "pdf", "扫描件未识别出文字层，请改用文字版 PDF"),
        ("数据库原理-实验数据.zip", "zip", "不支持的文件类型"),
    ]
    for filename, ftype, error in failed:
        if db.scalar(select(Document.id).where(Document.filename == filename)) is None:
            db.add(
                Document(
                    filename=filename,
                    file_type=ftype,
                    file_size=random.randint(800_000, 4_000_000),
                    stored_path="",
                    status="failed",
                    course=filename.split("-")[0],
                    is_seed=0,
                    chunk_count=0,
                    error=error,
                    uploaded_by=admin.id,
                    created_at=_rand_time(30),
                )
            )
            made += 1
    db.commit()
    return made


def seed_conversations(db, students: list[User], bases: list[KnowledgeBase]) -> int:
    if (db.scalar(select(func.count(Conversation.id))) or 0) > 40:
        return 0   # 已经造过了，重复运行不再堆
    base_names = [b.name for b in bases]
    made = 0
    for _ in range(80):
        user = random.choice(students)
        q, a, course = random.choice(QA_POOL)
        if course not in base_names:
            course = random.choice(base_names)
        created = _rand_time(30, weight_recent=True)
        conv = Conversation(
            user_id=user.id,
            title=q[:24],
            knowledge_bases=json.dumps([course], ensure_ascii=False),
            last_knowledge_bases=json.dumps([course], ensure_ascii=False),
            created_at=created,
        )
        db.add(conv)
        db.commit()
        db.refresh(conv)

        rounds = random.randint(1, 3)
        for r in range(rounds):
            qq, aa, cc = (q, a, course) if r == 0 else random.choice(QA_POOL)
            ts = created + timedelta(minutes=r * 2 + 1)
            db.add(
                Message(
                    conversation_id=conv.id,
                    role="user",
                    content=qq,
                    sources="[]",
                    interrupted=0,
                    created_at=ts,
                )
            )
            sources = json.dumps(
                [
                    {
                        "title": qq[:16],
                        "course": cc if cc in base_names else course,
                        "content": aa[:120],
                    }
                ],
                ensure_ascii=False,
            )
            db.add(
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content=aa,
                    sources=sources,
                    interrupted=1 if random.random() < 0.08 else 0,
                    created_at=ts + timedelta(seconds=random.randint(20, 90)),
                )
            )
        made += 1
    db.commit()
    return made


def seed_unanswered(db, students: list[User], bases: list[KnowledgeBase]) -> int:
    courses = [b.name for b in bases]
    existing = db.scalar(select(func.count(UnansweredQuestion.id))) or 0
    if existing > 30:
        return 0
    next_no = (db.scalar(select(func.max(UnansweredQuestion.display_no))) or 0) + 1

    made = 0
    pool = UNANSWERED_POOL[:]
    random.shuffle(pool)
    for i, (question, course) in enumerate(pool):
        if course not in courses:
            course = random.choice(courses)
        created = _rand_time(35)
        resolved = i % 3 == 0
        q = UnansweredQuestion(
            display_no=next_no + i,
            question=question,
            course=course,
            status="resolved" if resolved else "pending",
            related_points="[]",
            answer=random.choice(ANSWER_POOL) if resolved else "",
            answered_at=created + timedelta(days=1) if resolved else None,
            read_at=created + timedelta(days=2) if resolved and i % 2 == 0 else None,
            created_by=random.choice(students).id,
            created_at=created,
            updated_at=created,
        )
        db.add(q)
        made += 1

    # 补几条完全重复的提问，用来演示「按问题聚类」
    for question, _course in random.sample(UNANSWERED_POOL, 4):
        base = random.choice(courses)
        db.add(
            UnansweredQuestion(
                display_no=next_no + made,
                question=question + "？",
                course=base,
                status="pending",
                related_points="[]",
                answer="",
                created_by=random.choice(students).id,
                created_at=_rand_time(12),
                updated_at=NOW,
            )
        )
        made += 1
    db.commit()
    return made


def seed_recycle(db, admin: User) -> int:
    existing = db.scalar(select(func.count(RecycleItem.id))) or 0
    if existing > 6:
        return 0
    made = 0
    for name, count, days in (("Web 开发技术", 6, 3), ("大学物理（旧版）", 4, 12), ("程序设计基础", 5, 25)):
        if db.scalar(select(RecycleItem.id).where(RecycleItem.name == name)) is not None:
            continue
        payload = {
            "base": {"name": name, "description": f"{name}（演示用，已删除）"},
            "points": [
                {
                    "title": f"{name} 知识点 {i + 1}",
                    "content": f"{name} 的演示知识点内容，恢复后会重新向量化。",
                    "course": name,
                    "tags": "演示",
                    "created_by": admin.id,
                    "is_auto": 0,
                }
                for i in range(count)
            ],
            "docs": [],
        }
        db.add(
            RecycleItem(
                kind="base",
                name=name,
                payload=json.dumps(payload, ensure_ascii=False),
                deleted_by=admin.id,
                deleted_at=NOW - timedelta(days=days),
            )
        )
        made += 1
    db.commit()
    return made


def main() -> None:
    parser = argparse.ArgumentParser(description="生成管理端演示数据")
    parser.add_argument("--no-vectors", action="store_true", help="不调用 embedding（快）")
    args = parser.parse_args()

    init_db()
    with SessionLocal() as db:
        admin, students = seed_users(db)
        print(f"用户：学生 {len(students)} 个（含演示账号，密码都是 {DEMO_PASSWORD}）")

        bases = seed_bases(db, admin)
        print(f"知识库：共 {len(bases)} 个")

        points = seed_points(db, admin, not args.no_vectors)
        print(f"知识点：新增 {points} 条" + ("（已向量化）" if not args.no_vectors else "（未向量化）"))

        docs = seed_documents(db, admin)
        print(f"文档：新增 {docs} 条（含失败样例）")

        convs = seed_conversations(db, students, bases)
        print(f"会话：新增 {convs} 个")

        uq = seed_unanswered(db, students, bases)
        print(f"反馈：新增 {uq} 条")

        rc = seed_recycle(db, admin)
        print(f"回收站：新增 {rc} 条")

        print("\n当前总量：")
        for label, model in (
            ("用户", User),
            ("知识库", KnowledgeBase),
            ("知识点", KnowledgePoint),
            ("文档", Document),
            ("会话", Conversation),
            ("消息", Message),
            ("反馈", UnansweredQuestion),
            ("回收站", RecycleItem),
        ):
            print(f"  {label}: {db.scalar(select(func.count(model.id)))}")


if __name__ == "__main__":
    main()

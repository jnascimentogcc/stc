      *----------------------------------------------------------------
      * LNSIMCA - SIMULATION COMMAREA (LNSIMUL / LNRATE / LNELIG)
      *----------------------------------------------------------------
           05  CA-CUST-ID          PIC 9(10).
           05  CA-PROD-CD          PIC X(02).
           05  CA-PRINC-AMT        PIC S9(11)V99 COMP-3.
           05  CA-TERM-MO          PIC 9(03).
           05  CA-AMORT-TP         PIC X(01).
           05  CA-AGE              PIC 9(03).
           05  CA-INC-AMT          PIC S9(9)V99  COMP-3.
           05  CA-SCORE            PIC 9(04).
           05  CA-RATE-MO          PIC 9V9(6)    COMP-3.
           05  CA-INST-AMT         PIC S9(11)V99 COMP-3.
           05  CA-IOF-AMT          PIC S9(11)V99 COMP-3.
           05  CA-RC               PIC X(02).
               88  CA-RC-OK                VALUE '00'.
               88  CA-RJ-AGE               VALUE '01'.
               88  CA-RJ-INC               VALUE '02'.
               88  CA-RJ-SCORE             VALUE '03'.
               88  CA-RJ-CMT               VALUE '04'.
               88  CA-RJ-NOFFER            VALUE '05'.
               88  CA-RJ-PROD              VALUE '06'.
               88  CA-RJ-CUST              VALUE '07'.

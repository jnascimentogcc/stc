      *----------------------------------------------------------------
      * LATEREC - LATE INSTALLMENT (BANK.LOANS.LATE.*), LRECL 59
      *----------------------------------------------------------------
       01  LATE-REC.
           05  LR-CONTR-ID         PIC 9(10).
           05  LR-INST-NO          PIC 9(03).
           05  LR-DAYS-LATE        PIC 9(05).
           05  LR-INST-AMT         PIC 9(11)V99.
           05  LR-MORA-AMT         PIC 9(11)V99.
           05  LR-FINE-AMT         PIC 9(11)V99.
           05  LR-PROD-CD          PIC X(02).

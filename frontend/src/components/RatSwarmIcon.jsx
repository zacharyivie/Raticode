// Vector trace of review variant C, "Rats with tails".
// The square canvas centers the wide pair; strokes inherit the tab's color.
export default function RatSwarmIcon({ size = 24, strokeWidth = 1.5, ...props }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="180 175 900 900"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth * 30}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <path d="M 655 606 C 660 553 631 518 584 498 C 544 481 496 449 524 440 C 583 424 612 388 665 411 C 653 359 714 344 736 390 C 754 424 726 439 707 439 C 751 459 740 478 796 486 C 903 505 968 614 919 734 C 898 776 974 790 1020 749 C 1058 715 1046 683 1018 655" />
      <path d="M 767 620 C 718 643 726 694 749 725 Q 771 749 770 756 M 738 757 C 790 755 820 766 865 779 C 913 793 977 784 1011 758" />
      <path d="M 322 848 C 405 861 390 745 354 697 C 330 664 313 660 277 649 C 236 636 202 613 226 604 C 281 585 310 540 370 561 C 359 516 408 497 434 525 C 460 554 438 582 412 587 C 451 609 462 614 510 610 C 624 596 704 667 696 778 Q 696 802 685 825 C 671 863 758 889 811 866" />
      <path d="M 500 738 C 455 751 453 794 480 825 L 511 856 M 428 869 C 453 846 554 856 609 872 C 686 896 774 891 850 852" />
    </svg>
  );
}
